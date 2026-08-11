import asyncio
import logging
import re
import socket
from typing import ClassVar

from vhotplug.misc import is_unix_socket_alive, wait_for_unix_socket
from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo

logger = logging.getLogger("vhotplug")


class CrosvmVMUnavailableError(RuntimeError):
    """Raised when a Crosvm control socket is unavailable."""


class CrosvmUSBAttachStateError(RuntimeError):
    """Raised when Crosvm attached a USB device without identifying its port."""


class CrosvmLink:
    vm_retry_count = 5
    vm_retry_timeout = 1
    vm_wait_after_boot = 3
    vm_boot_timeout = 10
    vfio_command_timeout: float = 10
    usb_locks: ClassVar[dict[str, asyncio.Lock]] = {}

    def __init__(self, socket_path: str, crosvm_bin: str | None) -> None:
        self.socket_path = socket_path
        if crosvm_bin:
            self.crosvm_bin = crosvm_bin
        else:
            self.crosvm_bin = "crosvm"

    def _wait_for_boot(self) -> bool:
        """Waits for a crosvm vm to boot."""
        return wait_for_unix_socket(
            self.socket_path, self.vm_boot_timeout, self.vm_wait_after_boot, socket.SOCK_SEQPACKET
        )

    async def ensure_pci_hotplug(self) -> None:
        """Verify the live VM exposes the required VFIO control interface."""
        if not await asyncio.to_thread(self._wait_for_boot):
            raise CrosvmVMUnavailableError(f"Crosvm VM is unavailable at {self.socket_path}")
        try:
            await self.pci_list()
        except RuntimeError as error:
            raise RuntimeError(f"Crosvm at {self.socket_path} has no usable VFIO list interface: {error}") from error

    async def add_usb_device(self, usb_info: USBInfo, known_port: int | None = None) -> int:
        async with self.usb_locks.setdefault(self.socket_path, asyncio.Lock()):
            return await self._add_usb_device(usb_info, known_port)

    async def _add_usb_device(self, usb_info: USBInfo, known_port: int | None = None) -> int:
        dev_node = usb_info.device_node
        assert dev_node is not None, "Device node must be set"

        # Crosvm requires the kernel to be booted before USB devices can be passed through
        if not self._wait_for_boot():
            logger.warning("VM is not booted while adding device %s", dev_node)

        last_error: Exception | None = None
        for attempt in range(self.vm_retry_count + 1):
            try:
                logger.info("Adding USB device %s to %s", dev_node, self.socket_path)

                # Reuse a persisted port after a daemon restart if it still
                # contains the expected device. VID/PID alone is not a unique
                # identity because multiple identical USB devices may exist.
                devices = await self.usb_list()
                for port, vid, pid in devices:
                    if port == known_port and vid == usb_info.vid and pid == usb_info.pid:
                        logger.info(
                            "Device %s is already attached to %s on port %s, skipping",
                            dev_node,
                            self.socket_path,
                            port,
                        )
                        return port

                proc = await asyncio.create_subprocess_exec(
                    self.crosvm_bin,
                    "usb",
                    "attach",
                    "00:00:00:00",
                    dev_node,
                    self.socket_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_bytes, stderr_bytes = await proc.communicate()
                assert stdout_bytes is not None
                assert stderr_bytes is not None
                stdout_str = stdout_bytes.decode()
                stderr_str = stderr_bytes.decode()

                if proc.returncode != 0:
                    last_error = RuntimeError(f"Crosvm USB attach failed with code {proc.returncode}")
                    logger.warning(
                        "Failed to add device %s, error code: %s",
                        dev_node,
                        proc.returncode,
                    )
                    logger.warning("Out: %s", stdout_str)
                    logger.warning("Err: %s", stderr_str)
                else:
                    r = self._parse_usb_attach_response(stdout_str)
                    if r[0] == "ok":
                        if len(r) == 2:
                            try:
                                port = self._parse_usb_port(r[1])
                            except ValueError:
                                logger.warning("Crosvm returned an invalid USB port: %s", r[1])
                            else:
                                logger.info("Attached USB device %s, id: %s", dev_node, port)
                                return port
                        port = await self._recover_attached_usb_port(usb_info, devices)
                        logger.info("Attached USB device %s, id: %s", dev_node, port)
                        return port
                    if r[0] == "no_available_port":
                        # This can be transient while the guest xHCI driver is
                        # starting, or permanent when every port is occupied.
                        # Never detach unrelated devices to make room.
                        logger.info("No Crosvm USB port is available yet")
                    else:
                        last_error = RuntimeError("Unexpected Crosvm USB attach response")
                        logger.warning("Unexpected result: %s", r[0])
                        logger.warning("Out: %s", stdout_str)
                        logger.warning("Err: %s", stderr_str)
            except CrosvmUSBAttachStateError:
                raise
            except (OSError, RuntimeError, ValueError) as e:
                last_error = e
                logger.warning("Failed to attach USB device %s: %s", dev_node, e)

            if attempt < self.vm_retry_count:
                logger.info("Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
        logger.error("Failed to add USB device %s after %s attempts", dev_node, self.vm_retry_count + 1)
        raise RuntimeError("Crosvm USB attach timed out") from last_error

    @staticmethod
    def _parse_usb_port(value: str) -> int:
        port = int(value)
        if not 0 <= port <= 255:
            raise ValueError(f"Invalid Crosvm USB port: {port}")
        return port

    @staticmethod
    def _parse_usb_attach_response(stdout: str) -> list[str]:
        result = stdout.split()
        if not result:
            raise RuntimeError("Crosvm returned an empty USB attach response")
        return result

    @staticmethod
    def _single_new_usb_port(matches: list[int]) -> int:
        if len(matches) != 1:
            raise CrosvmUSBAttachStateError("Crosvm attached the USB device but did not report its port")
        return matches[0]

    async def _recover_attached_usb_port(
        self,
        usb_info: USBInfo,
        devices_before: list[tuple[int, str, str]],
    ) -> int:
        try:
            devices_after = await self.usb_list()
        except RuntimeError as e:
            raise CrosvmUSBAttachStateError(
                "Crosvm attached the USB device but its port could not be determined"
            ) from e
        old_ports = {port for port, _, _ in devices_before}
        matches = [
            port
            for port, vid, pid in devices_after
            if port not in old_ports and vid == usb_info.vid and pid == usb_info.pid
        ]
        return self._single_new_usb_port(matches)

    async def remove_usb_device_by_id(self, dev_id: int) -> None:
        try:
            logger.info("Detaching USB device %s from %s", dev_id, self.socket_path)
            proc = await asyncio.create_subprocess_exec(
                self.crosvm_bin,
                "usb",
                "detach",
                str(dev_id),
                self.socket_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            assert stdout_bytes is not None
            assert stderr_bytes is not None
            stdout_str = stdout_bytes.decode()
            stderr_str = stderr_bytes.decode()

            if proc.returncode != 0:
                logger.error("Failed to detach USB device, error code: %s", proc.returncode)
                logger.error("Out: %s", stdout_str)
                logger.error("Err: %s", stderr_str)
                raise RuntimeError(proc.returncode)
            r = stdout_str.split()
            if not r or r[0] != "ok":
                result = r[0] if r else "empty response"
                logger.error("Unexpected result: %s", result)
                logger.error("Out: %s", stdout_str)
                logger.error("Err: %s", stderr_str)
                raise RuntimeError(result)
            logger.info("Detached USB device %s", dev_id)
            return
        except OSError as e:
            logger.exception("Failed to detach USB device")
            raise RuntimeError(e) from None

    async def usb_list(self) -> list[tuple[int, str, str]]:
        devices: list[tuple[int, str, str]] = []
        try:
            logger.debug("Getting a list of USB devices from %s", self.socket_path)
            proc = await asyncio.create_subprocess_exec(
                self.crosvm_bin,
                "usb",
                "list",
                self.socket_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            assert stdout_bytes is not None
            assert stderr_bytes is not None
            stdout_str = stdout_bytes.decode()
            stderr_str = stderr_bytes.decode()

            if proc.returncode != 0:
                logger.error("Crosvm USB list failed with code %s: %s", proc.returncode, stderr_str.strip())
                if not is_unix_socket_alive(self.socket_path, socket.SOCK_SEQPACKET):
                    raise CrosvmVMUnavailableError("Crosvm VM is unavailable")
                raise RuntimeError(f"Crosvm USB list failed with code {proc.returncode}")

            result = stdout_str.split()
            if not result or result[0] != "devices" or len(result[1:]) % 3 != 0:
                logger.error("Malformed Crosvm USB list response: %s", stdout_str.strip())
                raise RuntimeError("Malformed Crosvm USB list response")

            data = result[1:]
            for i in range(0, len(data), 3):
                index = self._parse_usb_port(data[i])
                vid = data[i + 1]
                pid = data[i + 2]
                devices.append((index, vid, pid))
                logger.debug("USB device %s: %s:%s", index, vid, pid)

        except (OSError, ValueError) as e:
            logger.exception("Failed to list USB devices")
            raise RuntimeError(e) from None
        return devices

    async def remove_usb_device(self, usb_info: USBInfo, known_port: int | None = None) -> None:
        async with self.usb_locks.setdefault(self.socket_path, asyncio.Lock()):
            await self._remove_usb_device(usb_info, known_port)

    async def _remove_usb_device(self, usb_info: USBInfo, known_port: int | None = None) -> None:
        devices = await self.usb_list()
        if known_port is not None:
            device = next((dev for dev in devices if dev[0] == known_port), None)
            if device is None:
                logger.debug("USB port %s is already empty", known_port)
                return

            _, crosvm_vid, crosvm_pid = device
            if usb_info.vid and usb_info.pid and (usb_info.vid != crosvm_vid or usb_info.pid != crosvm_pid):
                logger.error(
                    "USB port %s now contains %s:%s instead of %s:%s; not detaching it",
                    known_port,
                    crosvm_vid,
                    crosvm_pid,
                    usb_info.vid,
                    usb_info.pid,
                )
                raise RuntimeError("Crosvm USB port contains a different device; refusing to detach")

            await self.remove_usb_device_by_id(known_port)
            return

        matches = [
            index
            for index, crosvm_vid, crosvm_pid in devices
            if usb_info.vid == crosvm_vid and usb_info.pid == crosvm_pid
        ]
        if len(matches) > 1:
            logger.error("Multiple Crosvm USB devices match %s:%s", usb_info.vid, usb_info.pid)
            raise RuntimeError("Crosvm USB device cannot be identified safely")
        if matches:
            await self.remove_usb_device_by_id(matches[0])

    async def add_pci_device(self, _pci_info: PCIInfo) -> None:
        pci_path = self._pci_path(_pci_info)
        if not await asyncio.to_thread(self._wait_for_boot):
            logger.warning("VM is not booted while adding PCI device %s", pci_path)

        added = False
        last_error: RuntimeError | None = None
        for attempt in range(self.vm_retry_count + 1):
            try:
                if pci_path in await self.pci_list():
                    if not added:
                        logger.info("PCI device %s is already attached", pci_path)
                    return
                if not added:
                    await self._run_vfio_command("add", pci_path)
                    added = True
                    if pci_path in await self.pci_list():
                        return
                last_error = RuntimeError("Crosvm accepted VFIO add but the device did not appear")
            except RuntimeError as error:
                last_error = error
                logger.warning("Failed to attach PCI device %s: %s", pci_path, error)
            if attempt < self.vm_retry_count:
                await asyncio.sleep(self.vm_retry_timeout)

        raise RuntimeError(
            f"Timed out attaching PCI device {pci_path} after {self.vm_retry_count + 1} attempts: {last_error}"
        ) from last_error

    async def remove_pci_device(self, _pci_info: PCIInfo) -> None:
        pci_path = self._pci_path(_pci_info)
        removed = False
        last_error: RuntimeError | None = None
        for attempt in range(self.vm_retry_count + 1):
            try:
                if pci_path not in await self.pci_list():
                    if not removed:
                        logger.info("PCI device %s is already detached", pci_path)
                    return
                if not removed:
                    await self._run_vfio_command("remove", pci_path)
                    removed = True
                    if pci_path not in await self.pci_list():
                        return
                last_error = RuntimeError("Crosvm accepted VFIO remove but the device is still attached")
            except RuntimeError as error:
                last_error = error
                logger.warning("Failed to detach PCI device %s: %s", pci_path, error)
            if attempt < self.vm_retry_count:
                await asyncio.sleep(self.vm_retry_timeout)

        raise RuntimeError(
            f"Timed out detaching PCI device {pci_path} after {self.vm_retry_count + 1} attempts: {last_error}"
        ) from last_error

    async def pci_list(self) -> list[str]:
        stdout = await self._run_vfio_command("list")
        result = stdout.split()
        if not result or result[0] != "devices":
            raise RuntimeError(f"Malformed Crosvm VFIO list response: {stdout.strip()}")
        devices = result[1:]
        unexpected = [
            entry
            for entry in devices
            if re.fullmatch(r"/sys/bus/pci/devices/[0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]", entry) is None
        ]
        if unexpected:
            raise RuntimeError(f"Unexpected entries in Crosvm VFIO list response: {unexpected}")
        return devices

    async def is_pci_dev_connected(self, pci_info: PCIInfo) -> bool:
        await self.ensure_pci_hotplug()
        pci_path = self._pci_path(pci_info)
        last_error: RuntimeError | None = None
        for attempt in range(self.vm_retry_count + 1):
            try:
                return pci_path in await self.pci_list()
            except RuntimeError as error:
                last_error = error
                logger.warning("Failed to query PCI device %s: %s", pci_path, error)
            if attempt < self.vm_retry_count:
                await asyncio.sleep(self.vm_retry_timeout)
        raise RuntimeError(f"Timed out querying PCI device {pci_path}: {last_error}") from last_error

    async def wait_until_pci_removed(self, pci_info: PCIInfo) -> None:
        pci_path = self._pci_path(pci_info)
        last_error: RuntimeError | None = None
        for attempt in range(self.vm_retry_count + 1):
            try:
                if pci_path not in await self.pci_list():
                    return
            except RuntimeError as error:
                last_error = error
                logger.warning("Failed to query PCI device %s during removal: %s", pci_path, error)
            if attempt < self.vm_retry_count:
                await asyncio.sleep(self.vm_retry_timeout)
        raise RuntimeError(f"Timed out waiting for PCI device removal {pci_path}: {last_error}") from last_error

    async def _run_vfio_command(self, command: str, pci_path: str | None = None) -> str:
        args = [self.crosvm_bin, "vfio", command]
        if pci_path is not None:
            args.append(pci_path)
        args.append(self.socket_path)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=self.vfio_command_timeout
                )
            except TimeoutError as error:
                proc.kill()
                await proc.wait()
                raise RuntimeError(f"Crosvm VFIO {command} timed out on {self.socket_path}") from error
        except OSError as error:
            raise RuntimeError(f"Failed to execute Crosvm VFIO {command}: {error}") from error

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace").strip()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Crosvm VFIO {command} failed on {self.socket_path} with code {proc.returncode}: "
                f"{stderr or stdout.strip()}"
            )
        return stdout

    @staticmethod
    def _pci_path(pci_info: PCIInfo) -> str:
        return f"/sys/bus/pci/devices/{pci_info.address}"

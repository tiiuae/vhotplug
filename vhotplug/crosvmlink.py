import asyncio
import logging
import socket

from vhotplug.misc import wait_for_unix_socket
from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo

logger = logging.getLogger("vhotplug")


class CrosvmLink:
    vm_retry_count = 5
    vm_retry_timeout = 1
    vm_wait_after_boot = 3
    vm_boot_timeout = 10

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

    async def add_usb_device(self, usb_info: USBInfo, known_port: int | None = None) -> int:
        dev_node = usb_info.device_node
        assert dev_node is not None, "Device node must be set"

        # Crosvm requires the kernel to be booted before USB devices can be passed through
        if not self._wait_for_boot():
            logger.warning("VM is not booted while adding device %s", dev_node)

        i = 0
        while True:
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
                    logger.warning(
                        "Failed to add device %s, error code: %s",
                        dev_node,
                        proc.returncode,
                    )
                    logger.warning("Out: %s", stdout_str)
                    logger.warning("Err: %s", stderr_str)
                else:
                    r = stdout_str.split()
                    if not r:
                        logger.warning("Crosvm returned an empty response")
                        r = [""]
                    if r[0] == "ok":
                        if len(r) == 2:
                            port = int(r[1])
                            logger.info("Attached USB device %s, id: %s", dev_node, port)
                            return port
                        logger.warning("Malformed Crosvm USB attach response: %s", stdout_str.strip())
                    elif r[0] == "no_available_port":
                        # This can be transient while the guest xHCI driver is
                        # starting, or permanent when every port is occupied.
                        # Never detach unrelated devices to make room.
                        logger.info("No Crosvm USB port is available yet")
                    else:
                        logger.warning("Unexpected result: %s", r[0])
                        logger.warning("Out: %s", stdout_str)
                        logger.warning("Err: %s", stderr_str)
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning("Failed to attach USB device %s: %s", dev_node, e)

            if i < self.vm_retry_count:
                logger.info("Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
                i += 1
            else:
                break
        logger.error("Failed to add USB device %s after %s attempts", dev_node, i)
        raise RuntimeError("Timeout")

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
            if r[0] != "ok":
                logger.error("Unexpected result: %s", r[0])
                logger.error("Out: %s", stdout_str)
                logger.error("Err: %s", stderr_str)
                raise RuntimeError(r[0])
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
                raise RuntimeError(f"Crosvm USB list failed with code {proc.returncode}: {stderr_str.strip()}")

            result = stdout_str.split()
            if not result or result[0] != "devices" or len(result[1:]) % 3 != 0:
                raise RuntimeError(f"Malformed Crosvm USB list response: {stdout_str.strip()}")

            data = result[1:]
            for i in range(0, len(data), 3):
                index = int(data[i])
                vid = data[i + 1]
                pid = data[i + 2]
                devices.append((index, vid, pid))
                logger.debug("USB device %s: %s:%s", index, vid, pid)

        except OSError as e:
            logger.exception("Failed to list USB devices")
            raise RuntimeError(e) from None
        return devices

    async def remove_usb_device(self, usb_info: USBInfo, known_port: int | None = None) -> None:
        devices = await self.usb_list()
        if known_port is not None:
            device = next((dev for dev in devices if dev[0] == known_port), None)
            if device is None:
                logger.debug("USB port %s is already empty", known_port)
                return

            _, crosvm_vid, crosvm_pid = device
            if usb_info.vid and usb_info.pid and (usb_info.vid != crosvm_vid or usb_info.pid != crosvm_pid):
                logger.warning(
                    "USB port %s now contains %s:%s instead of %s:%s; not detaching it",
                    known_port,
                    crosvm_vid,
                    crosvm_pid,
                    usb_info.vid,
                    usb_info.pid,
                )
                return

            await self.remove_usb_device_by_id(known_port)
            return

        matches = [
            index
            for index, crosvm_vid, crosvm_pid in devices
            if usb_info.vid == crosvm_vid and usb_info.pid == crosvm_pid
        ]
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple Crosvm USB devices match {usb_info.vid}:{usb_info.pid}; guest port is unknown"
            )
        if matches:
            await self.remove_usb_device_by_id(matches[0])

    async def add_pci_device(self, _pci_info: PCIInfo) -> None:
        raise RuntimeError("Not implemented")

    async def remove_pci_device(self, _pci_info: PCIInfo) -> None:
        raise RuntimeError("Not implemented")

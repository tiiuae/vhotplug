import asyncio
import logging

from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo
from vhotplug.vmm import wait_for_boot_crosvm

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

    # pylint: disable = too-many-branches
    async def add_usb_device(self, usb_info: USBInfo) -> None:
        dev_node = usb_info.device_node
        assert dev_node is not None, "Device node must be set"

        # Crosvm requires the kernel to be booted before USB devices can be passed through
        if not wait_for_boot_crosvm(self.socket_path, self.vm_boot_timeout, self.vm_wait_after_boot):
            logger.warning("VM is not booted while adding device %s", dev_node)

        i = 0
        while True:
            try:
                logger.info("Adding USB device %s to %s", dev_node, self.socket_path)

                # Check if the device is already connected
                devices = await self.usb_list()
                for _, vid, pid in devices:
                    if vid == usb_info.vid and pid == usb_info.pid:
                        logger.info(
                            "Device %s:%s is already attached to %s, skipping",
                            vid,
                            pid,
                            self.socket_path,
                        )
                        return

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
                    if r[0] == "ok":
                        logger.info("Attached USB device %s, id: %s", dev_node, r[1])
                        return
                    if r[0] == "no_available_port":
                        # Crosvm supports attaching USB devices only after the kernel has booted
                        # Here, we may attempt to attach a device before that which will return no_available_port
                        # If we keep trying, it may eventually return I/O error and USB passthrough won't work until the VM is rebooted
                        # As a workaround we remove USB devices here even if it returns no_such_device
                        # This helps prevent I/O errors and allows USB to be successfully attached once the VM boots
                        logger.info("No available port, removing all devices")
                        devices = await self.usb_list()
                        for index, _, _ in devices:
                            await self.remove_usb_device_by_id(index)
                    else:
                        logger.warning("Unexpected result: %s", r[0])
                        logger.warning("Out: %s", stdout_str)
                        logger.warning("Err: %s", stderr_str)
            except OSError as e:
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
                logger.error("Failed to get USB list, error code: %s", proc.returncode)
                logger.error("Out: %s", stdout_str)
                logger.error("Err: %s", stderr_str)
            else:
                r = stdout_str.split()
                if r[0] != "devices":
                    logger.error("Unexpected result: %s", r[0])
                    logger.error("Out: %s", stdout_str)
                    logger.error("Err: %s", stderr_str)
                else:
                    data = r[1:]
                    for i in range(0, len(data), 3):
                        index = int(data[i])
                        vid = data[i + 1]
                        pid = data[i + 2]
                        devices.append((index, vid, pid))
                        logger.debug("USB device %s: %s:%s", index, vid, pid)

        except OSError:
            logger.exception("Failed to list USB devices")
        return devices

    async def remove_usb_device(self, usb_info: USBInfo) -> None:
        devices = await self.usb_list()
        for index, crosvm_vid, crosvm_pid in devices:
            if usb_info.vid == crosvm_vid and usb_info.pid == crosvm_pid:
                logger.debug("Removing %s from %s", index, self.socket_path)
                await self.remove_usb_device_by_id(index)

    async def add_pci_device(self, _pci_info: PCIInfo) -> None:
        raise RuntimeError("Not implemented")

    async def remove_pci_device(self, _pci_info: PCIInfo) -> None:
        raise RuntimeError("Not implemented")

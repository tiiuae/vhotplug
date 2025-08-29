import logging
import asyncio
import subprocess
import os
import time
import socket
from vhotplug.usb import get_usb_info

logger = logging.getLogger("vhotplug")

class CrosvmLink:
    vm_retry_count = 5
    vm_retry_timeout = 1
    vm_boot_time = 3
    vm_boot_timeout = 10

    def __init__(self, socket_path, crosvm_bin):
        self.socket_path = socket_path
        if crosvm_bin:
            self.crosvm_bin = crosvm_bin
        else:
            self.crosvm_bin = "crosvm"

    def is_socket_alive(self):
        if not os.path.exists(self.socket_path):
            return False
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            client.connect(self.socket_path)
            client.close()
            return True
        except OSError as e:
            logger.warning("Socket %s is not alive: %s", self.socket_path, e)
        return False

    async def wait_for_boot(self):
        for attempt in range(1, self.vm_boot_timeout + 1):
            if self.is_socket_alive():
                stat = os.stat(self.socket_path)
                uptime = time.time() - stat.st_ctime
                logger.info("VM uptime: %s seconds, attempt %s", int(uptime), attempt)
                if uptime >= self.vm_boot_time:
                    return True
            else:
                logger.warning("VM is not running")
            await asyncio.sleep(1)
        return False

    # pylint: disable = too-many-branches
    async def add_usb_device(self, device):
        dev_node = device.device_node
        i = 0
        while True:
            try:
                logger.info("Adding USB device %s to %s", dev_node, self.socket_path)

                # Crosvm requires the kernel to be booted before USB devices can be passed through
                booted = await self.wait_for_boot()
                if not booted:
                    logger.error("VM is not booted while adding device %s", dev_node)

                # Check if the device is already connected
                devices = await self.usb_list()
                usb_info = get_usb_info(device)
                for index, vid, pid in devices:
                    if vid == usb_info.vid and pid == usb_info.pid:
                        logger.info("Device %s:%s is already attached to %s, skipping", vid, pid, self.socket_path)
                        return

                result = subprocess.run([self.crosvm_bin, "usb", "attach", "00:00:00:00", dev_node, self.socket_path], capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    logger.error("Failed to add device %s, error code: %s", dev_node, result.returncode)
                    logger.error("Out: %s", result.stdout)
                    logger.error("Err: %s", result.stderr)
                else:
                    r = result.stdout.split()
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
                            await self.remove_usb_device(index)
                    else:
                        logger.error("Unexpected result: %s", r[0])
                        logger.error("Out: %s", result.stdout)
                        logger.error("Err: %s", result.stderr)
            except OSError as e:
                logger.error("Failed to attach USB device %s: %s", dev_node, e)

            if i < self.vm_retry_count:
                logger.info("Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
                i += 1
            else:
                break
        logger.error("Failed to add USB device %s after %s attempts", dev_node, i)

    async def remove_usb_device(self, dev_id):
        try:
            logger.info("Detaching USB device %s from %s", dev_id, self.socket_path)
            result = subprocess.run([self.crosvm_bin, "usb", "detach", str(dev_id), self.socket_path], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.error("Failed to detach USB device, error code: %s", result.returncode)
                logger.error("Out: %s", result.stdout)
                logger.error("Err: %s", result.stderr)
            else:
                r = result.stdout.split()
                if r[0] == "ok":
                    logger.info("Detached USB device %s", dev_id)
                else:
                    logger.error("Unexpected result: %s", r[0])
                    logger.error("Out: %s", result.stdout)
                    logger.error("Err: %s", result.stderr)
        except OSError as e:
            logger.error("Failed to detach USB device: %s", e)

    async def usb_list(self):
        devices = []
        try:
            logger.debug("Getting a list of USB devices from %s", self.socket_path)
            result = subprocess.run([self.crosvm_bin, "usb", "list", self.socket_path], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.error("Failed to get USB list, error code: %s", result.returncode)
                logger.error("Out: %s", result.stdout)
                logger.error("Err: %s", result.stderr)
            else:
                r = result.stdout.split()
                if r[0] != "devices":
                    logger.error("Unexpected result: %s", r[0])
                    logger.error("Out: %s", result.stdout)
                    logger.error("Err: %s", result.stderr)
                else:
                    data = r[1:]
                    for i in range(0, len(data), 3):
                        index = int(data[i])
                        vid = data[i + 1]
                        pid = data[i + 2]
                        devices.append((index, vid, pid))
                        logger.debug("USB device %s: %s:%s", index, vid, pid)

        except OSError as e:
            logger.error("Failed to list USB devices: %s", e)
        return devices

import logging
import asyncio
import subprocess
from vhotplug.config import Config

logger = logging.getLogger("vhotplug")

class CrosvmLink:
    vm_retry_count = 5
    vm_retry_timeout = 1

    def __init__(self, socket_path, crosvm_bin):
        self.socket_path = socket_path
        if crosvm_bin:
            self.crosvm_bin = crosvm_bin
        else:
            self.crosvm_bin = "crosvm"

    async def add_usb_device(self, device):
        dev_node = device.device_node
        i = 0
        while True:
            try:
                logger.info(f"Adding USB device {dev_node} to {self.socket_path}")
                result = subprocess.run([self.crosvm_bin, "usb", "attach", "00:00:00:00", dev_node, self.socket_path], capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"Failed to add device {dev_node}, error code: {result.returncode}")
                    logger.error(f"Out: {result.stdout}")
                    logger.error(f"Err: {result.stderr}")
                else:
                    r = result.stdout.split()
                    if r[0] == "ok":
                        logger.info(f"Attached USB device {dev_node}, id: {r[1]}")
                        return
                    elif r[0] == "no_available_port":
                        # Crosvm supports attaching USB devices only after the kernel has booted
                        # Here, we may attempt to attach a device before that which will return no_available_port
                        # If we keep trying, it may eventually return I/O error and USB passthrough won't work until the VM is rebooted
                        # As a workaround we remove USB devices here even if it returns no_such_device
                        # This helps prevent I/O errors and allows USB to be successfully attached once the VM boots
                        logger.info(f"No available port, removing all devices")
                        devices = await self.usb_list()
                        for index, _, _ in devices:
                            await self.remove_usb_device(index)
                    else:
                        logger.error(f"Unexpected result: {r[0]}")
                        logger.error(f"Out: {result.stdout}")
                        logger.error(f"Err: {result.stderr}")
            except Exception as e:
                logger.error(f"Failed to attach USB device {dev_node}: {e}")

            if i < self.vm_retry_count:
                logger.info(f"Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
                i += 1
            else:
                break
        logger.error(f"Failed to add USB device {dev_node} after {i} attempts")

    async def remove_usb_device(self, id):
        try:
            logger.info(f"Detaching USB device {id} from {self.socket_path}")
            result = subprocess.run([self.crosvm_bin, "usb", "detach", str(id), self.socket_path], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to detach USB device, error code: {result.returncode}")
                logger.error(f"Out: {result.stdout}")
                logger.error(f"Err: {result.stderr}")
            else:
                r = result.stdout.split()
                if r[0] == "ok":
                    logger.info(f"Detached USB device {id}")
                else:
                    logger.error(f"Unexpected result: {r[0]}")
                    logger.error(f"Out: {result.stdout}")
                    logger.error(f"Err: {result.stderr}")
        except Exception as e:
            logger.error(f"Failed to detach USB device: {e}")

    async def usb_list(self):
        devices = []
        try:
            logger.debug(f"Getting a list of USB devices from {self.socket_path}")
            result = subprocess.run([self.crosvm_bin, "usb", "list", self.socket_path], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Failed to get USB list, error code: {result.returncode}")
                logger.error(f"Out: {result.stdout}")
                logger.error(f"Err: {result.stderr}")
            else:
                r = result.stdout.split()
                if r[0] != "devices":
                    logger.error(f"Unexpected result: {r[0]}")
                    logger.error(f"Out: {result.stdout}")
                    logger.error(f"Err: {result.stderr}")
                else:
                    data = r[1:]
                    for i in range(0, len(data), 3):
                        index = int(data[i])
                        vid = data[i + 1]
                        pid = data[i + 2]
                        devices.append((index, vid, pid))
                        logger.info(f"USB device {index}: {vid}:{pid}")

        except Exception as e:
            logger.error(f"Failed to list USB devices: {e}")
        return devices

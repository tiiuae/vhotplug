from qemu.qmp import QMPClient
import logging
import asyncio
import re

logger = logging.getLogger("vhotplug")

class QEMULink:
    retry_count = 5
    retry_timeout = 1

    def __init__(self, socket_path):
        self.socket_path = socket_path

    async def wait_for_vm(self):
        while True:
            try:
                status = await self.query_status()
                if status == "running":
                    logger.info("The VM is running")
                    break
                else:
                    logger.info(f"VM status: {status}")
            except Exception as e:
                logger.error(f"Failed to query VM status: {e}")
            await asyncio.sleep(1)

    async def query_commands(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-commands")
            logger.info("QMP Commands:")
            for x in res:
                logger.info(x)
        except Exception as e:
            logger.error(f"Failed to get a list of commands: {e}")
        finally:
            await qmp.disconnect()

    async def usb(self):
        ids = []
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("human-monitor-command", {"command-line": "info usb"})
            logger.debug(f"Guest USB Devices:")
            for line in res.splitlines():
                logger.debug(f"{line}")
                id_pattern = re.compile(r',\sID:\s(\w+)')
                match = id_pattern.search(line)
                if match:
                    ids.append(match.group(1))
        except Exception as e:
            logger.error(f"Failed to get a list of USB guest devices: {e}")
        finally:
            await qmp.disconnect()
        return ids

    async def usbhost(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("human-monitor-command", {"command-line": "info usbhost"})
            logger.info(f"Host USB Devices:")
            for line in res.splitlines():
                logger.info(f"{line}")
        except Exception as e:
            logger.error(f"Failed to get a list of USB host devices: {e}")
        finally:
            await qmp.disconnect()

    async def query_status(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-status")
            return res['status']
        except:
            pass
        finally:
            await qmp.disconnect()

    async def query_pci(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-pci")
            logger.info("Guest PCI Devices:")
            for x in res:
                for dev in x['devices']:
                    class_info = dev['class_info']
                    logger.info(f"  Description: {class_info.get('desc')}. Class: {class_info.get('class')}. Bus: {dev['bus']}. Slot {dev['slot']}.")
                    logger.debug(dev)
        except Exception as e:
            logger.error(f"Failed to query PCI: {e}")
        finally:
            await qmp.disconnect()

    def id_for_usb(self, device):
        busnum = int(device.properties.get("BUSNUM"))
        devnum = int(device.properties.get("DEVNUM"))
        return f"usb{busnum}{devnum}"

    async def add_usb_device(self, device):
        busnum = int(device.properties.get("BUSNUM"))
        devnum = int(device.properties.get("DEVNUM"))
        qemuid = self.id_for_usb(device)
        i = 0
        while True:
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                logger.debug(f"Adding USB device with id {qemuid} to {self.socket_path}")
                res = await qmp.execute("device_add", {"driver": "usb-host", "hostbus": busnum, "hostaddr": devnum, "id": qemuid})
                if res:
                    logger.error(f"Failed to add device {qemuid}: {res}")
                else:
                    logger.info(f"Attached USB device: {qemuid}")
                return
            except Exception as e:
                if str(e).startswith("Duplicate device ID"):
                    logger.info(f"USB device {qemuid} is already attached to the VM")
                    return
                else:
                    logger.error(f"Failed to add USB device {qemuid}: {e}")
                    i += 1
            finally:
                await qmp.disconnect()

            if i < self.retry_count:
                logger.info(f"Retrying")
                await asyncio.sleep(self.retry_timeout)
            else:
                break
        logger.error(f"Failed to add USB device: {qemuid}")

    async def remove_usb_device(self, device):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            qemuid = self.id_for_usb(device)
            res = await qmp.execute("device_del", {"id": qemuid})
            if res:
                logger.error(f"Failed to remove USB device {qemuid}: {res}")
            else:
                logger.info(f"Removed USB device {qemuid} from {self.socket_path}")
        except Exception as e:
            if str(e) == f"Device '{qemuid}' not found":
                logger.debug(f"Failed to remove USB device {qemuid} from {self.socket_path}: {e}")
            else:
                logger.error(f"Failed to remove USB device {qemuid} from {self.socket_path}: {e}")
        finally:
            await qmp.disconnect()

    async def add_evdev_device(self, device, bus):
        idindex = 0
        i = 0
        while True:
            qemuid = device.sys_name
            if idindex > 0:
                qemuid += f"-{idindex}"
            logger.debug(f"Adding evdev device {device.device_node} with id {qemuid} to bus {bus}")
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                res = await qmp.execute("device_add", {"driver": "virtio-input-host-pci", "evdev": device.device_node, "id": qemuid, "bus": bus})
                if res:
                    logger.error(f"Failed to add evdev device to bus {bus}: {res}")
                else:
                    logger.info(f"Attached evdev device {device.device_node} to bus {bus}")
                    return
            except Exception as e:
                if str(e).startswith("Duplicate device ID"):
                    idindex += 1
                elif str(e).endswith("Device or resource busy"):
                    logger.info("The device is busy, it is likely already connected to the VM")
                    return
                else:
                    logger.error(f"Failed to add evdev device to bus {bus}: {e}")
                    i += 1
            finally:
                await qmp.disconnect()

            if i < self.retry_count:
                logger.info(f"Retrying")
                await asyncio.sleep(self.retry_timeout)
            else:
                break
        logger.error(f"Failed to add evdev device: {device.device_node}")

    async def remove_evdev_device(self, device):
        logger.debug(f"Removing evdev device {device.device_node} with id {device.sys_name}")
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("device_del", {"id": device.sys_name})
            if res:
                logger.error(f"Failed to remove evdev device: {res}")
            else:
                logger.debug(f"Removed evdev device {device.sys_name}")
        except Exception as e:
            logger.error(f"Failed to remove evdev device: {e}")
        finally:
            await qmp.disconnect()

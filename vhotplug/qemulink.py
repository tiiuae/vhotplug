import logging
import asyncio
import re
from qemu.qmp import QMPClient, QMPError

logger = logging.getLogger("vhotplug")

class QEMULink:
    vm_retry_count = 5
    vm_retry_timeout = 1

    def __init__(self, socket_path):
        self.socket_path = socket_path

    async def wait_for_vm(self):
        while True:
            try:
                status = await self.query_status()
                if status == "running":
                    logger.info("The VM is running")
                    break
                logger.info("VM status: %s", status)
            except QMPError as e:
                logger.error("Failed to query VM status: %s", e)
            await asyncio.sleep(1)

    async def query_commands(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-commands")
            logger.info("QMP Commands:")
            for x in res:
                logger.info(x)
        except QMPError as e:
            logger.error("Failed to get a list of commands: %s", e)
        finally:
            await qmp.disconnect()

    async def usb(self):
        ids = []
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("human-monitor-command", {"command-line": "info usb"})
            logger.debug("Guest USB Devices:")
            for line in res.splitlines():
                logger.debug("%s", line)
                id_pattern = re.compile(r',\sID:\s(\w+)')
                match = id_pattern.search(line)
                if match:
                    ids.append(match.group(1))
        except QMPError as e:
            logger.error("Failed to get a list of USB guest devices: %s", e)
        finally:
            await qmp.disconnect()
        return ids

    async def usbhost(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("human-monitor-command", {"command-line": "info usbhost"})
            logger.info("Host USB Devices:")
            for line in res.splitlines():
                logger.info("%s", line)
        except QMPError as e:
            logger.error("Failed to get a list of USB host devices: %s", e)
        finally:
            await qmp.disconnect()

    async def query_status(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-status")
            return res['status']
        except QMPError as e:
            logger.debug("Failed to query status: %s", e)
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
                    logger.info("  Description: %s. Class: %s. Bus: %s. Slot %s.", class_info.get('desc'), class_info.get('class'), dev['bus'], dev['slot'])
                    logger.debug(dev)
        except QMPError as e:
            logger.error("Failed to query PCI: %s", e)
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
                logger.info("Adding USB device with id %s bus %s dev %s to %s", qemuid, busnum, devnum, self.socket_path)
                res = await qmp.execute("device_add", {"driver": "usb-host", "hostbus": busnum, "hostaddr": devnum, "id": qemuid})
                if res:
                    logger.error("Failed to add device %s: %s", qemuid, res)
                else:
                    logger.info("Attached USB device: %s", qemuid)
                return
            except QMPError as e:
                if str(e).startswith("Duplicate device ID"):
                    logger.info("USB device %s is already attached to the VM", qemuid)
                    return
                logger.error("Failed to add USB device %s: %s", qemuid, e)
                i += 1
            finally:
                await qmp.disconnect()

            if i < self.vm_retry_count:
                logger.info("Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
            else:
                break
        logger.error("Failed to add USB device: %s", qemuid)

    async def add_usb_device_by_vid_pid(self, device, vid, pid):
        qemuid = self.id_for_usb(device)
        i = 0
        while True:
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                logger.debug("Adding USB device %s:%s with id %s to %s", vid, pid, qemuid, self.socket_path)
                res = await qmp.execute("device_add", {"driver": "usb-host", "vendorid": int(vid, 16), "productid": int(pid, 16), "id": qemuid})
                if res:
                    logger.error("Failed to add device %s:%s with id %s: %s", vid, pid, qemuid, res)
                else:
                    logger.info("Attached USB device %s:%s with id %s", vid, pid, qemuid)
                return
            except QMPError as e:
                if str(e).startswith("Duplicate device ID"):
                    logger.info("USB device %s:%s with id %s is already attached to the VM", vid, pid, qemuid)
                    return
                logger.error("Failed to add USB device %s:%s with id %s: %s", vid, pid, qemuid, e)
                i += 1
            finally:
                await qmp.disconnect()

            if i < self.vm_retry_count:
                logger.info("Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
            else:
                break
        logger.error("Failed to add USB device %s:%s with id %s", vid, pid, qemuid)

    async def remove_usb_device(self, device):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            qemuid = self.id_for_usb(device)
            res = await qmp.execute("device_del", {"id": qemuid})
            if res:
                logger.error("Failed to remove USB device %s: %s", qemuid, res)
            else:
                logger.info("Removed USB device %s from %s", qemuid, self.socket_path)
        except QMPError as e:
            if str(e) == f"Device '{qemuid}' not found":
                logger.debug("Failed to remove USB device %s from %s: %s", qemuid, self.socket_path, e)
            else:
                logger.error("Failed to remove USB device %s from %s: %s", qemuid, self.socket_path, e)
        finally:
            await qmp.disconnect()

    async def add_evdev_device(self, device, bus):
        idindex = 0
        i = 0
        while True:
            qemuid = device.sys_name
            if idindex > 0:
                qemuid += f"-{idindex}"
            logger.debug("Adding evdev device %s with id %s to bus %s", device.device_node, qemuid, bus)
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                res = await qmp.execute("device_add", {"driver": "virtio-input-host-pci", "evdev": device.device_node, "id": qemuid, "bus": bus})
                if res:
                    logger.error("Failed to add evdev device to bus %s: %s", bus, res)
                else:
                    logger.info("Attached evdev device %s to bus %s", device.device_node, bus)
                    return
            except QMPError as e:
                if str(e).startswith("Duplicate device ID"):
                    idindex += 1
                elif str(e).endswith("Device or resource busy"):
                    logger.info("The device is busy, it is likely already connected to the VM")
                    return
                else:
                    logger.error("Failed to add evdev device to bus %s: %s", bus, e)
                    i += 1
            finally:
                await qmp.disconnect()

            if i < self.vm_retry_count:
                logger.info("Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
            else:
                break
        logger.error("Failed to add evdev device: %s", device.device_node)

    async def remove_evdev_device(self, device):
        logger.debug("Removing evdev device %s with id %s", device.device_node, device.sys_name)
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("device_del", {"id": device.sys_name})
            if res:
                logger.error("Failed to remove evdev device: %s", res)
            else:
                logger.debug("Removed evdev device %s", device.sys_name)
        except QMPError as e:
            logger.error("Failed to remove evdev device: %s", e)
        finally:
            await qmp.disconnect()

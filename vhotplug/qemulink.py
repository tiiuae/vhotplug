import logging
import asyncio
import re
from qemu.qmp import QMPClient, QMPError
from vhotplug.vmm import wait_for_boot_qemu

logger = logging.getLogger("vhotplug")

# QEMU QMP Reference Manual: https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html

class QEMULink:
    vm_retry_count = 5
    vm_retry_timeout = 1
    vm_boot_timeout = 5

    def __init__(self, socket_path):
        self.socket_path = socket_path

    def _qemu_id_usb(self, usb_info):
        return f"usb{usb_info.busnum}{usb_info.devnum}"

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

    async def query_usb(self):
        """This command is unstable/experimental and meant for debugging."""

        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("x-query-usb")
            # Example: '  Device 0.1, Port 1, Speed 12 Mb/s, Product host:3.2, ID: usb32\n'
            logger.info("USB: %s", res['human-readable-text'])
        except QMPError as e:
            logger.error("Failed to get a list of commands: %s", e)
        finally:
            await qmp.disconnect()

    async def query_pci(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-pci")
            logger.debug("PCI: %s", res)
            return res
        except QMPError as e:
            logger.error("Failed to query PCI: %s", e)
        finally:
            await qmp.disconnect()

    async def print_pci(self):
        res = await self.query_pci()
        if not res:
            return

        # pylint: disable = too-many-locals
        def walk_devices(devices, indent=0):
            for dev in devices:
                qdev_id = dev.get("qdev_id")
                vid = f"{dev["id"].get("vendor", 0):04x}"
                did = f"{dev["id"].get("device", 0):04x}"
                domain = 0
                bus = dev.get("bus", 0)
                slot = dev.get("slot", 0)
                func = dev.get("function", 0)
                address = f"{domain:04x}:{bus:02x}:{slot:02x}.{func:x}"
                class_info = dev.get("class_info", {})
                pci_class = f"{class_info.get('class', 0):04x}"
                pci_class_desc = class_info.get("desc")
                logger.info("%sPCI %s, ID: %s, VID: %s, DID: %s, class: %s (%s)", " " * indent, address, qdev_id, vid, did, pci_class, pci_class_desc)

                pci_bridge = dev.get("pci_bridge")
                if pci_bridge:
                    bus_info = pci_bridge.get("bus", {})
                    secondary = bus_info.get("secondary")
                    subdevs = pci_bridge.get("devices", [])
                    new_indent = indent + 2
                    logger.info("%sBridge to bus %s with %d devices", " " * new_indent, secondary, len(subdevs))
                    if subdevs:
                        walk_devices(subdevs, new_indent)
                    else:
                        logger.info("%sEmpty bridge", " " * new_indent)

        logger.info("Guest PCI devices:")
        for root_bus in res:
            walk_devices(root_bus["devices"], 2)

    async def add_usb_device(self, usb_info):
        if not wait_for_boot_qemu(self.socket_path, self.vm_boot_timeout, 0):
            logger.warning("VM is not booted while adding device %s", usb_info.device_node)

        qemuid = self._qemu_id_usb(usb_info)
        i = 0
        while True:
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                logger.info("Adding USB device with id %s bus %s dev %s to %s", qemuid, usb_info.busnum, usb_info.devnum, self.socket_path)
                res = await qmp.execute("device_add", {"driver": "usb-host", "hostbus": usb_info.busnum, "hostaddr": usb_info.devnum, "id": qemuid})
                if res:
                    logger.error("Failed to add device %s: %s", qemuid, res)
                else:
                    logger.info("Attached USB device: %s", qemuid)
                    return
            except QMPError as e:
                if str(e).startswith("Duplicate device ID"):
                    logger.info("USB device %s is already attached to the VM", qemuid)
                    return
                logger.warning("Failed to add USB device %s: %s", qemuid, e)
                i += 1
            finally:
                await qmp.disconnect()

            if i < self.vm_retry_count:
                logger.info("Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
            else:
                break
        logger.error("Failed to add USB device %s after %s attempts", qemuid, i)
        raise RuntimeError("Timeout")

    async def add_usb_device_by_vid_pid(self, usb_info):
        if not wait_for_boot_qemu(self.socket_path, self.vm_boot_timeout, 0):
            logger.warning("VM is not booted while adding device %s", usb_info.device_node)

        qemuid = self._qemu_id_usb(usb_info)
        i = 0
        while True:
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                logger.debug("Adding USB device %s:%s with id %s to %s", usb_info.vid, usb_info.pid, qemuid, self.socket_path)
                res = await qmp.execute("device_add", {"driver": "usb-host", "vendorid": int(usb_info.vid, 16), "productid": int(usb_info.pid, 16), "id": qemuid})
                if res:
                    logger.error("Failed to add device %s:%s with id %s: %s", usb_info.vid, usb_info.pid, qemuid, res)
                else:
                    logger.info("Attached USB device %s:%s with id %s", usb_info.vid, usb_info.pid, qemuid)
                    return
            except QMPError as e:
                if str(e).startswith("Duplicate device ID"):
                    logger.info("USB device %s:%s with id %s is already attached to the VM", usb_info.vid, usb_info.pid, qemuid)
                    return
                logger.warning("Failed to add USB device %s:%s with id %s: %s", usb_info.vid, usb_info.pid, qemuid, e)
                i += 1
            finally:
                await qmp.disconnect()

            if i < self.vm_retry_count:
                logger.info("Retrying")
                await asyncio.sleep(self.vm_retry_timeout)
            else:
                break
        logger.error("Failed to add USB device %s:%s with id %s after %s attempts", usb_info.vid, usb_info.pid, qemuid, i)
        raise RuntimeError("Timeout")

    async def remove_usb_device(self, usb_info):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            qemuid = self._qemu_id_usb(usb_info)
            res = await qmp.execute("device_del", {"id": qemuid})
            if res:
                logger.error("Failed to remove USB device %s: %s", qemuid, res)
                raise RuntimeError(res)
            logger.info("Removed USB device %s from %s", qemuid, self.socket_path)
        except QMPError as e:
            if str(e) == f"Device '{qemuid}' not found":
                logger.debug("Failed to remove USB device %s from %s: %s", qemuid, self.socket_path, e)
            else:
                logger.error("Failed to remove USB device %s from %s: %s", qemuid, self.socket_path, e)
                raise RuntimeError(e) from None
        finally:
            await qmp.disconnect()

    async def add_evdev_device(self, device, bus):
        if not wait_for_boot_qemu(self.socket_path, self.vm_boot_timeout, 0):
            logger.warning("VM is not booted while adding device %s", device.device_node)

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

    async def add_pci_device(self, pci_info):
        if not wait_for_boot_qemu(self.socket_path, self.vm_boot_timeout, 0):
            logger.warning("VM is not booted while adding device %s", pci_info.friendly_name())

        #await self.print_pci()

        qemuid = await self._find_pci_device(pci_info)
        if qemuid:
            logger.info("PCI device %s is already attached to the VM with id %s", pci_info.friendly_name(), qemuid)
            return

        bus = await self._find_empty_pci_bridge()
        if bus:
            logger.info("Found empty PCI bridge: %s", bus)
        else:
            logger.warning("Could not find an empty PCI bridge in the VM")

        qemuid = pci_info.runtime_id()
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            logger.info("Adding PCI device with id %s address %s to %s", qemuid, pci_info.address, self.socket_path)
            res = await qmp.execute("device_add", {"driver": "vfio-pci", "host": pci_info.address, "id": qemuid, "bus": bus})
            if res:
                raise RuntimeError(f"Failed to add PCI device {pci_info.friendly_name()}: {res}")
            logger.info("Attached PCI device: %s", qemuid)
            return
        except QMPError as e:
            if str(e).startswith("Duplicate device ID"):
                logger.info("PCI device %s is already attached to the VM with id %s", pci_info.friendly_name(), qemuid)
                return
            raise RuntimeError(f"Failed to add PCI device {pci_info.friendly_name()}: {e}") from e
        finally:
            await qmp.disconnect()

    async def _remove_pci_device_by_qemu_id(self, qemuid):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("device_del", {"id": qemuid})
            if res:
                logger.error("Failed to remove PCI device %s: %s", qemuid, res)
                raise RuntimeError(res)
            logger.info("Removed PCI device %s from %s", qemuid, self.socket_path)
        except QMPError as e:
            if str(e) == f"Device '{qemuid}' not found":
                logger.debug("Failed to remove PCI device %s from %s: %s", qemuid, self.socket_path, e)
            else:
                logger.error("Failed to remove PCI device %s from %s: %s", qemuid, self.socket_path, e)
                raise RuntimeError(e) from None
        finally:
            await qmp.disconnect()

    async def remove_pci_device(self, pci_info):
        return await self._remove_pci_device_by_qemu_id(pci_info.runtime_id())

    async def _find_pci_device(self, pci_info):
        res = await self.query_pci()
        if not res:
            return None

        def walk_devices(devices):
            for dev in devices:
                guest_vid = dev["id"].get("vendor")
                guest_did = dev["id"].get("device")
                vid_match = pci_info.vendor_id and guest_vid and pci_info.vendor_id == guest_vid
                did_match = pci_info.device_id and guest_did and pci_info.device_id == guest_did

                if vid_match and did_match:
                    return dev.get("qdev_id")

                pci_bridge = dev.get("pci_bridge")
                if pci_bridge:
                    subdevs = pci_bridge.get("devices", [])
                    if subdevs:
                        qemu_id = walk_devices(subdevs)
                        if qemu_id:
                            return qemu_id

            return None

        for root_bus in res:
            qemu_id = walk_devices(root_bus["devices"])
            if qemu_id:
                return qemu_id

        return None

    async def _find_empty_pci_bridge(self):
        res = await self.query_pci()
        if not res:
            return None

        def walk_devices(devices):
            for dev in devices:
                pci_bridge = dev.get("pci_bridge")
                if pci_bridge:
                    subdevs = pci_bridge.get("devices", [])
                    if subdevs:
                        qemu_id = walk_devices(subdevs)
                        if qemu_id:
                            return qemu_id
                    else:
                        qemu_id = dev.get("qdev_id")
                        if qemu_id:
                            return qemu_id

            return None

        for root_bus in res:
            qemu_id = walk_devices(root_bus["devices"])
            if qemu_id:
                return qemu_id

        return None

    async def remove_pci_device_by_vid_did(self, pci_info):
        qemuid = await self._find_pci_device(pci_info)
        if qemuid is None:
            logger.error("PCI device %s not found in guest", pci_info.friendly_name())
            return

        return await self._remove_pci_device_by_qemu_id(qemuid)

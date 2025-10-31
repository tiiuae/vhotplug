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
    vm_boot_timeout = 1

    def __init__(self, socket_path):
        self.socket_path = socket_path
        self._lock = asyncio.Lock()

    async def _wait_for_vm(self):
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

    def _qemu_id_usb(self, usb_info):
        return f"usb{usb_info.busnum}{usb_info.devnum}"

    def _qemu_id_pci(self, pci_info):
        return pci_info.runtime_id()

    def _qemu_id_evdev(self, device):
        return f"evdev-{device.sys_name}"

    async def _execute(self, cmd, args = None, retry=True):
        last_error = None

        for attempt in range(1, self.vm_retry_count + 1):
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                res = await qmp.execute(cmd, args)

                if isinstance(res, dict) and "error" in res:
                    last_error = res["error"]
                    logger.warning("QMP command %s failed: %s", cmd, last_error)
                else:
                    return res

            except QMPError as e:
                last_error = e
                logger.warning("Failed to execute qemu command %s: %s", cmd, e)
            finally:
                await qmp.disconnect()

            if not retry:
                break
            if attempt < self.vm_retry_count:
                logger.info("Retrying in %s seconds...", self.vm_retry_timeout)
                await asyncio.sleep(self.vm_retry_timeout)

        raise RuntimeError(last_error)

    async def _execute_simple(self, cmd, args = None):
        try:
            return await self._execute(cmd, args, False)
        except RuntimeError as e:
            logger.error(str(e))
            return None

    async def query_commands(self):
        res = await self._execute_simple("query-commands")
        if res:
            logger.info("QMP Commands:")
            for x in res:
                logger.info(x)

    async def usb(self):
        """Returns a list of QEMU IDs for USB devices attached to the VM."""

        ids = []
        res = await self._execute_simple("human-monitor-command", {"command-line": "info usb"})
        if res:
            logger.debug("Guest USB Devices:")
            for line in res.splitlines():
                logger.debug("%s", line)
                id_pattern = re.compile(r',\sID:\s(\w+)')
                match = id_pattern.search(line)
                if match:
                    ids.append(match.group(1))
        return ids

    async def usbhost(self):
        res = await self._execute_simple("human-monitor-command", {"command-line": "info usbhost"})
        if res:
            logger.debug("Host USB Devices:")
            for line in res.splitlines():
                logger.info("%s", line)

    async def query_status(self):
        res = await self._execute_simple("query-status")
        if res:
            return res['status']

    async def query_usb(self):
        """This command is unstable/experimental and meant for debugging."""

        res = await self._execute_simple("x-query-usb")
        if res:
            # Example: '  Device 0.1, Port 1, Speed 12 Mb/s, Product host:3.2, ID: usb32\n'
            logger.info("USB: %s", res['human-readable-text'])

    async def query_pci(self):
        res = await self._execute_simple("query-pci")
        if res:
            logger.debug("PCI: %s", res)
            return res

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

    async def _find_usb_device(self, qemuid):
        devs = await self.usb()
        return qemuid in devs

    async def add_usb_device(self, usb_info):
        async with self._lock:
            if not wait_for_boot_qemu(self.socket_path, self.vm_boot_timeout, 0):
                logger.warning("VM is not booted while adding device %s", usb_info.friendly_name())
                return

            qemuid = self._qemu_id_usb(usb_info)
            if await self._find_usb_device(qemuid):
                logger.info("USB device %s is already attached to the VM with id %s", usb_info.friendly_name(), qemuid)
                return

            logger.info("Adding USB device with id %s bus %s dev %s to %s", qemuid, usb_info.busnum, usb_info.devnum, self.socket_path)
            await self._execute("device_add", {"driver": "usb-host", "hostbus": usb_info.busnum, "hostaddr": usb_info.devnum, "id": qemuid})
            logger.info("Attached USB device %s with id %s", usb_info.friendly_name(), qemuid)

    async def add_usb_device_by_vid_pid(self, usb_info):
        async with self._lock:
            if not wait_for_boot_qemu(self.socket_path, self.vm_boot_timeout, 0):
                logger.warning("VM is not booted while adding device %s", usb_info.friendly_name())
                return

            qemuid = self._qemu_id_usb(usb_info)
            if await self._find_usb_device(qemuid):
                logger.info("USB device %s is already attached to the VM with id %s", usb_info.friendly_name(), qemuid)
                return

            logger.info("Adding USB device %s:%s with id %s to %s", usb_info.vid, usb_info.pid, qemuid, self.socket_path)
            await self._execute("device_add", {"driver": "usb-host", "vendorid": int(usb_info.vid, 16), "productid": int(usb_info.pid, 16), "id": qemuid})
            logger.info("Attached USB device %s with id %s", usb_info.friendly_name(), qemuid)

    async def remove_usb_device(self, usb_info):
        async with self._lock:
            qemuid = self._qemu_id_usb(usb_info)
            await self._execute("device_del", {"id": qemuid})

    async def add_evdev_device(self, device, bus):
        async with self._lock:
            if not wait_for_boot_qemu(self.socket_path, self.vm_boot_timeout, 0):
                logger.warning("VM is not booted while adding device %s", device.device_node)
                return

            qemuid = self._qemu_id_evdev(device)
            logger.debug("Adding evdev device %s with id %s to bus %s", device.device_node, qemuid, bus)
            try:
                await self._execute("device_add", {"driver": "virtio-input-host-pci", "evdev": device.device_node, "id": qemuid, "bus": bus})
                logger.info("Attached evdev device %s to bus %s", device.device_node, bus)
            except RuntimeError as e:
                if str(e).endswith("Device or resource busy"):
                    logger.info("The device is busy, it is likely already connected to the VM")
                    return
                raise

    async def remove_evdev_device(self, device):
        async with self._lock:
            logger.debug("Removing evdev device %s with id %s", device.device_node, device.sys_name)
            qemuid = self._qemu_id_evdev(device)
            await self._execute("device_del", {"id": qemuid})
            logger.debug("Removed evdev device %s", device.sys_name)

    async def add_pci_device(self, pci_info):
        async with self._lock:
            if not wait_for_boot_qemu(self.socket_path, self.vm_boot_timeout, 0):
                logger.warning("VM is not booted while adding device %s", pci_info.friendly_name())
                return

            qemuid = await self._find_pci_device(pci_info)
            if qemuid:
                logger.info("PCI device %s is already attached to the VM with id %s", pci_info.friendly_name(), qemuid)
                return

            bus = await self._find_empty_pci_bridge()
            if bus:
                logger.info("Found empty PCI bridge: %s", bus)
            else:
                logger.warning("Could not find an empty PCI bridge in the VM")

            qemuid = self._qemu_id_pci(pci_info)
            logger.info("Adding PCI device %s with id %s to %s", pci_info.address, qemuid, self.socket_path)
            await self._execute("device_add", {"driver": "vfio-pci", "host": pci_info.address, "id": qemuid, "bus": bus})
            logger.info("Attached PCI device: %s", qemuid)

    async def _remove_pci_device_by_qemu_id(self, qemuid):
        await self._execute("device_del", {"id": qemuid}, False)
        logger.info("Removed PCI device %s from %s", qemuid, self.socket_path)

    async def remove_pci_device(self, pci_info):
        async with self._lock:
            qemuid = self._qemu_id_pci(pci_info)
            return await self._remove_pci_device_by_qemu_id(qemuid)

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
        async with self._lock:
            qemuid = await self._find_pci_device(pci_info)
            if qemuid is None:
                logger.error("PCI device %s not found in guest", pci_info.friendly_name())
                return

            return await self._remove_pci_device_by_qemu_id(qemuid)

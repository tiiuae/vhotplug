import asyncio
import logging
import re
import socket
from typing import Any

import pyudev
from qemu.qmp import QMPClient, QMPError

from vhotplug.misc import wait_for_unix_socket
from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo

logger = logging.getLogger("vhotplug")

# QEMU QMP Reference Manual: https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html


class QEMULink:
    vm_retry_count = 5
    vm_retry_timeout = 1
    vm_wait_after_boot = 0
    vm_boot_timeout = 1

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self._lock = asyncio.Lock()

    async def _wait_for_vm(self) -> None:
        while True:
            try:
                status = await self.query_status()
                if status == "running":
                    logger.info("The VM is running")
                    break
                logger.info("VM status: %s", status)
            except QMPError:
                logger.exception("Failed to query VM status")
            await asyncio.sleep(1)

    def _wait_for_boot(self) -> bool:
        """Waits for a qemu vm to boot."""
        return wait_for_unix_socket(self.socket_path, self.vm_boot_timeout, self.vm_wait_after_boot, socket.SOCK_STREAM)

    def _qemu_id_usb(self, usb_info: USBInfo) -> str:
        return f"usb{usb_info.busnum}{usb_info.devnum}"

    def _qemu_id_pci(self, pci_info: PCIInfo) -> str:
        return pci_info.runtime_id()

    def _qemu_id_evdev(self, device: pyudev.Device) -> str:
        return f"evdev-{device.sys_name}"

    async def _execute(
        self, cmd: str, args: dict[str, Any] | None = None, retry: bool = True
    ) -> dict[str, Any] | list[Any] | str:
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
                    # QMP returns object, but we know it's one of these types
                    assert isinstance(res, (dict, list, str)), f"Unexpected QMP response type: {type(res)}"
                    return res

            except QMPError as e:
                last_error = e
                logger.warning("Failed to execute qemu command %s: %s", cmd, e)
            finally:
                await qmp.disconnect()

            if not retry:
                break

            # Don't retry when PCI port is not available
            if re.search(r"PCI: slot \d+ function \d+ already occupied by", str(last_error)):
                break

            if attempt < self.vm_retry_count:
                logger.info("Retrying in %s seconds...", self.vm_retry_timeout)
                await asyncio.sleep(self.vm_retry_timeout)

        raise RuntimeError(last_error)

    async def _execute_simple(
        self, cmd: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any] | str | None:
        try:
            return await self._execute(cmd, args, False)
        except RuntimeError:
            logger.exception("Exception occurred")
            return None

    async def query_commands(self) -> None:
        res = await self._execute_simple("query-commands")
        if res:
            logger.info("QMP Commands:")
            for x in res:
                logger.info(x)

    async def usb(self) -> list[str]:
        """Returns a list of QEMU IDs for USB devices attached to the VM."""
        ids: list[str] = []
        res = await self._execute_simple("human-monitor-command", {"command-line": "info usb"})
        if res and isinstance(res, str):
            logger.debug("Guest USB Devices:")
            for line in res.splitlines():
                logger.debug("%s", line)
                id_pattern = re.compile(r",\sID:\s(\w+)")
                match = id_pattern.search(line)
                if match:
                    ids.append(match.group(1))
        return ids

    async def usbhost(self) -> None:
        res = await self._execute_simple("human-monitor-command", {"command-line": "info usbhost"})
        if res and isinstance(res, str):
            logger.debug("Host USB Devices:")
            for line in res.splitlines():
                logger.info("%s", line)

    async def query_status(self) -> str | None:
        res = await self._execute_simple("query-status")
        if res and isinstance(res, dict):
            return str(res["status"])
        return None

    async def query_usb(self) -> None:
        """This command is unstable/experimental and meant for debugging."""
        res = await self._execute_simple("x-query-usb")
        if res and isinstance(res, dict):
            # Example: '  Device 0.1, Port 1, Speed 12 Mb/s, Product host:3.2, ID: usb32\n'
            logger.info("USB: %s", res["human-readable-text"])

    async def query_pci(self) -> list[dict[str, Any]] | None:
        res = await self._execute_simple("query-pci")
        if res and isinstance(res, list):
            logger.debug("PCI: %s", res)
            return res
        return None

    async def print_pci(self) -> None:
        res = await self.query_pci()
        if not res:
            return

        def walk_devices(devices: list[dict[str, Any]], indent: int = 0) -> None:
            for dev in devices:
                qdev_id = dev.get("qdev_id")
                vid = f"{dev['id'].get('vendor', 0):04x}"
                did = f"{dev['id'].get('device', 0):04x}"
                domain = 0
                bus = dev.get("bus", 0)
                slot = dev.get("slot", 0)
                func = dev.get("function", 0)
                address = f"{domain:04x}:{bus:02x}:{slot:02x}.{func:x}"
                class_info = dev.get("class_info", {})
                pci_class = f"{class_info.get('class', 0):04x}"
                pci_class_desc = class_info.get("desc")
                logger.info(
                    "%sPCI %s, ID: %s, VID: %s, DID: %s, class: %s (%s)",
                    " " * indent,
                    address,
                    qdev_id,
                    vid,
                    did,
                    pci_class,
                    pci_class_desc,
                )

                pci_bridge = dev.get("pci_bridge")
                if pci_bridge:
                    bus_info = pci_bridge.get("bus", {})
                    secondary = bus_info.get("secondary")
                    subdevs = pci_bridge.get("devices", [])
                    new_indent = indent + 2
                    logger.info(
                        "%sBridge to bus %s with %d devices",
                        " " * new_indent,
                        secondary,
                        len(subdevs),
                    )
                    if subdevs:
                        walk_devices(subdevs, new_indent)
                    else:
                        logger.info("%sEmpty bridge", " " * new_indent)

        logger.info("Guest PCI devices:")
        for root_bus in res:
            walk_devices(root_bus["devices"], 2)

    async def _find_usb_device(self, qemuid: str) -> bool:
        devs = await self.usb()
        return qemuid in devs

    async def add_usb_device(self, usb_info: USBInfo) -> None:
        async with self._lock:
            if not self._wait_for_boot():
                logger.warning("VM is not booted while adding device %s", usb_info.friendly_name())
                return

            qemuid = self._qemu_id_usb(usb_info)
            if await self._find_usb_device(qemuid):
                logger.info(
                    "USB device %s is already attached to the VM with id %s",
                    usb_info.friendly_name(),
                    qemuid,
                )
                return

            logger.info(
                "Adding USB device with id %s bus %s dev %s to %s",
                qemuid,
                usb_info.busnum,
                usb_info.devnum,
                self.socket_path,
            )
            await self._execute(
                "device_add",
                {
                    "driver": "usb-host",
                    "hostbus": usb_info.busnum,
                    "hostaddr": usb_info.devnum,
                    "id": qemuid,
                },
            )
            logger.info("Attached USB device %s with id %s", usb_info.friendly_name(), qemuid)

    async def add_usb_device_by_vid_pid(self, usb_info: USBInfo) -> None:
        async with self._lock:
            if not self._wait_for_boot():
                logger.warning("VM is not booted while adding device %s", usb_info.friendly_name())
                return

            qemuid = self._qemu_id_usb(usb_info)
            if await self._find_usb_device(qemuid):
                logger.info(
                    "USB device %s is already attached to the VM with id %s",
                    usb_info.friendly_name(),
                    qemuid,
                )
                return

            logger.info(
                "Adding USB device %s:%s with id %s to %s",
                usb_info.vid,
                usb_info.pid,
                qemuid,
                self.socket_path,
            )
            assert usb_info.vid is not None and usb_info.pid is not None, "VID and PID must be set"
            await self._execute(
                "device_add",
                {
                    "driver": "usb-host",
                    "vendorid": int(usb_info.vid, 16),
                    "productid": int(usb_info.pid, 16),
                    "id": qemuid,
                },
            )
            logger.info("Attached USB device %s with id %s", usb_info.friendly_name(), qemuid)

    async def remove_usb_device(self, usb_info: USBInfo) -> None:
        async with self._lock:
            qemuid = self._qemu_id_usb(usb_info)
            await self._execute("device_del", {"id": qemuid})

    async def _add_pci_device(self, params: dict[str, Any]) -> None:
        """Adds PCI device to the first available PCI port."""
        ports = await self._find_empty_pci_bridges()
        if len(ports):
            logger.debug("Found %d empty PCI bridges: %s", len(ports), ports)
        else:
            logger.warning("Could not find any empty PCI bridges in the VM")

        for port in ports:
            try:
                logger.debug("Trying port %s", port)
                params["bus"] = port
                await self._execute("device_add", params)
                return
            except RuntimeError as e:
                if re.search(r"PCI: slot \d+ function \d+ already occupied by", str(e)):
                    logger.warning("PCI port %s is not available", port)
                    continue
                raise

        raise RuntimeError("No available PCI ports found")

    async def add_evdev_device(self, device: pyudev.Device) -> None:
        async with self._lock:
            if not self._wait_for_boot():
                logger.warning("VM is not booted while adding device %s", device.device_node)
                return

            qemuid = self._qemu_id_evdev(device)
            logger.debug(
                "Adding evdev device %s with id %s",
                device.device_node,
                qemuid,
            )
            try:
                await self._add_pci_device(
                    {
                        "driver": "virtio-input-host-pci",
                        "evdev": device.device_node,
                        "id": qemuid,
                    }
                )
                logger.info("Attached evdev device %s", device.device_node)
            except RuntimeError as e:
                if str(e).endswith("Device or resource busy"):
                    logger.info("The device is busy, it is likely already connected to the VM")
                    return

    async def remove_evdev_device(self, device: pyudev.Device) -> None:
        async with self._lock:
            logger.debug(
                "Removing evdev device %s with id %s",
                device.device_node,
                device.sys_name,
            )
            qemuid = self._qemu_id_evdev(device)
            await self._execute("device_del", {"id": qemuid})
            logger.debug("Removed evdev device %s", device.sys_name)

    async def add_pci_device(self, pci_info: PCIInfo) -> None:
        async with self._lock:
            if not self._wait_for_boot():
                logger.warning("VM is not booted while adding device %s", pci_info.friendly_name())
                return

            qemuid = await self._find_pci_device(pci_info)
            if qemuid is not None:
                logger.info(
                    "PCI device %s is already attached to the VM with id %s",
                    pci_info.friendly_name(),
                    qemuid,
                )
                return

            qemuid = self._qemu_id_pci(pci_info)
            logger.info(
                "Adding PCI device %s with id %s to %s",
                pci_info.address,
                qemuid,
                self.socket_path,
            )

            await self._add_pci_device(
                {
                    "driver": "vfio-pci",
                    "host": pci_info.address,
                    "id": qemuid,
                }
            )
            logger.info("Attached PCI device: %s", pci_info.friendly_name())

    async def _remove_pci_device_by_qemu_id(self, qemuid: str) -> None:
        await self._execute("device_del", {"id": qemuid}, False)
        logger.info("Removed PCI device %s from %s", qemuid, self.socket_path)

    async def remove_pci_device(self, pci_info: PCIInfo) -> None:
        async with self._lock:
            qemuid = self._qemu_id_pci(pci_info)
            await self._remove_pci_device_by_qemu_id(qemuid)

    async def _find_pci_device(self, pci_info: PCIInfo) -> str | None:
        res = await self._execute("query-pci")
        if not res:
            return None

        def walk_devices(devices: list[dict[str, Any]]) -> str | None:
            for dev in devices:
                guest_vid = dev["id"].get("vendor")
                guest_did = dev["id"].get("device")
                vid_match = pci_info.vendor_id and guest_vid and pci_info.vendor_id == guest_vid
                did_match = pci_info.device_id and guest_did and pci_info.device_id == guest_did

                # qdev_id can be an empty string when it's not set during passthrough
                if vid_match and did_match:
                    return dev.get("qdev_id")

                pci_bridge = dev.get("pci_bridge")
                if pci_bridge:
                    subdevs = pci_bridge.get("devices", [])
                    if subdevs:
                        qemu_id = walk_devices(subdevs)
                        if qemu_id is not None:
                            return qemu_id

            return None

        for root_bus in res:
            if isinstance(root_bus, dict):
                devices = root_bus["devices"]
                if not isinstance(devices, list):
                    continue
                qemu_id = walk_devices(devices)
                if qemu_id is not None:
                    return qemu_id

        return None

    async def _find_empty_pci_bridges(self) -> list[str]:
        res = await self._execute("query-pci")
        if not res:
            return []

        def walk_devices(devices: list[dict[str, Any]]) -> list[str]:
            qemu_ids: list[str] = []
            for dev in devices:
                pci_bridge = dev.get("pci_bridge")
                if pci_bridge:
                    subdevs = pci_bridge.get("devices", [])
                    if subdevs:
                        qemu_ids.extend(walk_devices(subdevs))
                    else:
                        qdev_id = dev.get("qdev_id")
                        if qdev_id:
                            qemu_ids.append(str(qdev_id))
            return qemu_ids

        all_qemu_ids: list[str] = []
        for root_bus in res:
            if isinstance(root_bus, dict):
                devices = root_bus.get("devices", [])
                if isinstance(devices, list):
                    all_qemu_ids.extend(walk_devices(devices))

        return all_qemu_ids

    async def remove_pci_device_by_vid_did(self, pci_info: PCIInfo) -> None:
        async with self._lock:
            qemuid = await self._find_pci_device(pci_info)
            if qemuid is None:
                logger.error("PCI device %s not found in guest", pci_info.friendly_name())
                return

            if qemuid == "":
                logger.error("PCI device %s qemu id is not set", pci_info.friendly_name())
                return

            await self._remove_pci_device_by_qemu_id(qemuid)

    async def pause(self) -> None:
        """Pauses VM execution."""
        async with self._lock:
            if not self._wait_for_boot():
                logger.warning("VM is not booted while trying to pause")
                return

            await self._execute("stop")
            logger.info("Paused %s", self.socket_path)

    async def resume(self) -> None:
        """Resumes VM execution."""
        async with self._lock:
            if not self._wait_for_boot():
                logger.warning("VM is not booted while trying to resume")
                return

            await self._execute("cont")
            logger.info("Resumed %s", self.socket_path)

    async def is_pci_dev_connected(self, pci_info: PCIInfo) -> bool:
        async with self._lock:
            if not self._wait_for_boot():
                raise RuntimeError("VM is not booted while checking PCI device")

            return await self._find_pci_device(pci_info) is not None

import json
import logging
import threading
from pathlib import Path

from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo

logger = logging.getLogger("vhotplug")


class DeviceState:
    def __init__(self, persistent: bool = False, db_path: str | None = None) -> None:
        self.persistent = persistent
        self.lock = threading.RLock()

        # Runtime map of USB device_node - VM, used to know from which VM to disconnect
        self.usb_device_vm_map: dict[str, str] = {}

        # Runtime map of PCI address - VM, used to know from which VM to disconnect
        self.pci_device_vm_map: dict[str, str] = {}

        # Persistent map for devices that have multiple VMs selected by the user
        self.selected_vms: dict[str, str] = {}

        # Persistent set of devices permanently disconnected by the user
        self.disconnected_devices: set[str] = set()

        # Load data from a JSON file if persistence is enabled
        if self.persistent:
            assert db_path is not None, "db_path must be provided when persistent=True"
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self) -> None:
        if self.persistent and self.db_path.exists():
            try:
                with self.db_path.open("r", encoding="utf-8") as f:
                    j = json.load(f)
                    self.selected_vms = j.get("selected_vms", {})
                    self.disconnected_devices = set(j.get("disconnected_devices", []))
            except OSError as e:
                logger.warning("Failed to load USB state database: %s", e)

    def _save(self) -> None:
        if self.persistent:
            with self.db_path.open("w", encoding="utf-8") as f:
                j = {
                    "selected_vms": self.selected_vms,
                    "disconnected_devices": list(self.disconnected_devices),
                }
                json.dump(j, f, ensure_ascii=False, indent=2)

    def set_vm_for_device(self, dev_info: USBInfo | PCIInfo, vm_name: str) -> None:
        with self.lock:
            if isinstance(dev_info, USBInfo):
                if dev_info.device_node is not None:
                    self.usb_device_vm_map[dev_info.device_node] = vm_name
            else:
                assert dev_info.address is not None, "PCI address cannot be None"
                self.pci_device_vm_map[dev_info.address] = vm_name

    def get_vm_for_device(self, dev_info: USBInfo | PCIInfo) -> str | None:
        with self.lock:
            if isinstance(dev_info, USBInfo):
                if dev_info.device_node is None:
                    return None
                return self.usb_device_vm_map.get(dev_info.device_node)
            return self.pci_device_vm_map.get(dev_info.address)

    def remove_vm_for_device(self, dev_info: USBInfo | PCIInfo) -> None:
        with self.lock:
            if isinstance(dev_info, USBInfo):
                if dev_info.device_node in self.usb_device_vm_map:
                    del self.usb_device_vm_map[dev_info.device_node]
            elif dev_info.address in self.pci_device_vm_map:
                del self.pci_device_vm_map[dev_info.address]

    def select_vm_for_device(self, dev_info: USBInfo | PCIInfo, vm_name: str) -> None:
        with self.lock:
            self.selected_vms[dev_info.persistent_id()] = vm_name
            self._save()

    def get_selected_vm_for_device(self, dev_info: USBInfo | PCIInfo) -> str | None:
        with self.lock:
            return self.selected_vms.get(dev_info.persistent_id())

    def clear_selected_vm_for_device(self, dev_info: USBInfo | PCIInfo) -> None:
        with self.lock:
            dev_id = dev_info.persistent_id()
            if dev_id in self.selected_vms:
                del self.selected_vms[dev_id]
                self._save()

    def set_disconnected(self, dev_info: USBInfo | PCIInfo) -> None:
        with self.lock:
            self.disconnected_devices.add(dev_info.persistent_id())
            self._save()

    def is_disconnected(self, dev_info: USBInfo | PCIInfo) -> bool:
        with self.lock:
            return dev_info.persistent_id() in self.disconnected_devices

    def clear_disconnected(self, dev_info: USBInfo | PCIInfo) -> bool:
        with self.lock:
            dev_id = dev_info.persistent_id()
            if dev_id in self.disconnected_devices:
                self.disconnected_devices.remove(dev_id)
                self._save()
                return True
        return False

    def list_usb_devices(self) -> dict[str, str]:
        with self.lock:
            return dict(self.usb_device_vm_map)

    def list_pci_devices(self) -> dict[str, str]:
        with self.lock:
            return dict(self.pci_device_vm_map)

    def list_disconnected(self) -> list[str]:
        with self.lock:
            return list(self.disconnected_devices)

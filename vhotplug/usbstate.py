import json
import threading
import logging
from pathlib import Path

logger = logging.getLogger("vhotplug")

class USBState:
    def __init__(self, persistent = False, db_path=None):
        self.persistent = persistent
        self.lock = threading.RLock()

        # Runtime map of device_node - VM, used to know from which VM to disconnect
        self.device_vm_map: dict[str, str] = {}

        # Persistent map for devices that have multiple VMs selected by the user
        self.selected_vms: dict[str, str] = {}

        # Persistent set of devices forcibly disconnected by the user
        self.disconnected_devices: set[str] = set()

        # Load data from a JSON file if persistence is enabled
        if self.persistent:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    def _load(self):
        if self.persistent and self.db_path.exists():
            try:
                with self.db_path.open("r", encoding="utf-8") as f:
                    j = json.load(f)
                    self.selected_vms = j.get("selected_vms", {})
                    self.disconnected_devices = set(j.get("disconnected_devices", []))
            except OSError as e:
                logger.warning("Failed to load USB state database: %s", e)

    def _save(self):
        if self.persistent:
            with self.db_path.open("w", encoding="utf-8") as f:
                j = {
                    "selected_vms": self.selected_vms,
                    "disconnected_devices": list(self.disconnected_devices),
                }
                json.dump(j, f, ensure_ascii=False, indent=2)

    def _persistent_usb_id(self, usb_info) -> str:
        return f"{usb_info.vid}:{usb_info.pid}:{usb_info.serial}"

    def set_vm_for_device(self, usb_info, vm_name: str):
        with self.lock:
            self.device_vm_map[usb_info.device_node] = vm_name

    def get_vm_for_device(self, usb_info):
        with self.lock:
            return self.device_vm_map.get(usb_info.device_node)

    def remove_vm_for_device(self, usb_info):
        with self.lock:
            if usb_info.device_node in self.device_vm_map:
                del self.device_vm_map[usb_info.device_node]

    def select_vm_for_device(self, usb_info, vm_name: str):
        with self.lock:
            self.selected_vms[self._persistent_usb_id(usb_info)] = vm_name
            self._save()

    def get_selected_vm_for_device(self, usb_info):
        with self.lock:
            return self.selected_vms.get(self._persistent_usb_id(usb_info))

    def clear_selected_vm_for_device(self, usb_info):
        usb_id = self._persistent_usb_id(usb_info)
        with self.lock:
            if usb_id in self.selected_vms:
                del self.selected_vms[usb_id]
                self._save()

    def set_disconnected(self, usb_info):
        with self.lock:
            self.disconnected_devices.add(self._persistent_usb_id(usb_info))
            self._save()

    def is_disconnected(self, usb_info) -> bool:
        with self.lock:
            return self._persistent_usb_id(usb_info) in self.disconnected_devices

    def clear_disconnected(self, usb_info) -> bool:
        usb_id = self._persistent_usb_id(usb_info)
        with self.lock:
            if usb_id in self.disconnected_devices:
                self.disconnected_devices.remove(usb_id)
                self._save()
                return True
        return False

    def list_devices(self) -> dict[str, str]:
        with self.lock:
            return dict(self.device_vm_map)

    def list_disconnected(self) -> list[str]:
        with self.lock:
            return list(self.disconnected_devices)

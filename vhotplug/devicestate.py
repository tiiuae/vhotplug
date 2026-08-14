import contextlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo

logger = logging.getLogger("vhotplug")


class DeviceState:
    def __init__(self, persistent: bool = False, db_path: str | None = None) -> None:
        self.persistent = persistent
        self.lock = threading.RLock()

        # Runtime map of USB device_node - VM, used to know from which VM to disconnect
        self.usb_device_vm_map: dict[str, str] = {}

        # Persistent map of USB topology - Crosvm guest binding. A guest port
        # is valid only for one VM control socket generation.
        self.crosvm_usb_port_map: dict[str, dict[str, Any]] = {}

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
                    j = self._state_object(json.load(f))
                    self.selected_vms = j.get("selected_vms", {})
                    self.disconnected_devices = set(j.get("disconnected_devices", []))
                    ports = j.get("crosvm_usb_ports", {})
                    if isinstance(ports, dict):
                        for key, value in ports.items():
                            if self._valid_crosvm_usb_binding(key, value):
                                self.crosvm_usb_port_map[key] = value
                            else:
                                logger.warning("Discarding invalid Crosvm USB binding for %s", key)
            except (OSError, TypeError, ValueError) as e:
                logger.warning("Failed to load state database, starting from empty state: %s", e)

    def _save(self) -> None:
        if self.persistent:
            tmp_path = self.db_path.with_name(f".{self.db_path.name}.tmp")
            try:
                with tmp_path.open("w", encoding="utf-8") as f:
                    os.chmod(tmp_path, 0o600)
                    j = {
                        "selected_vms": self.selected_vms,
                        "disconnected_devices": list(self.disconnected_devices),
                        "crosvm_usb_ports": self.crosvm_usb_port_map,
                    }
                    json.dump(j, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                tmp_path.replace(self.db_path)
            except OSError as e:
                logger.warning("Failed to save state database: %s", e)
                with contextlib.suppress(OSError):
                    tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _state_object(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("State database root must be an object")
        return value

    @staticmethod
    def _valid_port(port: object) -> bool:
        return type(port) is int and 0 <= port <= 255

    @classmethod
    def _valid_crosvm_usb_binding(cls, key: object, value: object) -> bool:
        if not isinstance(key, str) or not isinstance(value, dict):
            return False
        return (
            cls._valid_port(value.get("port"))
            and isinstance(value.get("vm"), str)
            and isinstance(value.get("socket_generation"), str)
            and (value.get("vid") is None or isinstance(value.get("vid"), str))
            and (value.get("pid") is None or isinstance(value.get("pid"), str))
            and (value.get("serial") is None or isinstance(value.get("serial"), str))
        )

    @staticmethod
    def _usb_key(dev_info: USBInfo) -> str | None:
        return dev_info.sys_name

    @staticmethod
    def _socket_generation(socket_path: str) -> str | None:
        try:
            stat = os.stat(socket_path)
        except OSError:
            return None
        return f"{stat.st_dev}:{stat.st_ino}:{stat.st_ctime_ns}"

    @staticmethod
    def _binding_matches_device(binding: dict[str, Any], dev_info: USBInfo) -> bool:
        return all(
            actual is None or binding.get(field) == actual
            for field, actual in (("vid", dev_info.vid), ("pid", dev_info.pid), ("serial", dev_info.serial))
        )

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
                key = self._usb_key(dev_info)
                if key in self.crosvm_usb_port_map:
                    del self.crosvm_usb_port_map[key]
                    self._save()
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

    def set_crosvm_usb_port(
        self,
        dev_info: USBInfo,
        vm_name: str,
        socket_path: str,
        port: int | None,
    ) -> None:
        with self.lock:
            key = self._usb_key(dev_info)
            if key is None:
                return
            if port is None:
                if self.crosvm_usb_port_map.pop(key, None) is None:
                    return
                self._save()
                return
            if not self._valid_port(port):
                raise ValueError(f"Invalid Crosvm USB port: {port}")
            socket_generation = self._socket_generation(socket_path)
            if socket_generation is None:
                logger.warning("Cannot persist Crosvm USB binding: socket %s is unavailable", socket_path)
                return
            binding = {
                "vm": vm_name,
                "port": port,
                "socket_generation": socket_generation,
                "vid": dev_info.vid,
                "pid": dev_info.pid,
                "serial": dev_info.serial,
            }
            if self.crosvm_usb_port_map.get(key) == binding:
                return
            self.crosvm_usb_port_map[key] = binding
            self._save()

    def get_crosvm_usb_port(self, dev_info: USBInfo, vm_name: str, socket_path: str) -> int | None:
        with self.lock:
            key = self._usb_key(dev_info)
            if key is None:
                return None
            binding = self.crosvm_usb_port_map.get(key)
            if binding is None:
                return None
            if (
                binding.get("vm") != vm_name
                or binding.get("socket_generation") != self._socket_generation(socket_path)
                or not self._binding_matches_device(binding, dev_info)
            ):
                del self.crosvm_usb_port_map[key]
                self._save()
                return None
            port = binding.get("port")
            return port if self._valid_port(port) else None

    def clear_crosvm_usb_ports(self, vm_names: list[str]) -> None:
        with self.lock:
            keys = [key for key, value in self.crosvm_usb_port_map.items() if value.get("vm") in vm_names]
            if not keys:
                return
            for key in keys:
                del self.crosvm_usb_port_map[key]
            self._save()

    def list_pci_devices(self) -> dict[str, str]:
        with self.lock:
            return dict(self.pci_device_vm_map)

    def list_disconnected(self) -> list[str]:
        with self.lock:
            return list(self.disconnected_devices)

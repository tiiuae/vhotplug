from typing import NamedTuple, Optional
import logging

logger = logging.getLogger("vhotplug")

class PCIInfo(NamedTuple):
    address: Optional[str] = None
    driver: Optional[str] = None
    vendor_id: Optional[int] = None
    device_id: Optional[int] = None
    vid: Optional[str] = None
    did: Optional[str] = None
    vendor_name: Optional[str] = None
    device_name: Optional[str] = None
    pci_class: Optional[int] = None
    pci_subclass: Optional[int] = None
    pci_prog_if: Optional[int] = None
    pci_subsystem_vendor_id: Optional[str] = None
    pci_subsystem_id: Optional[str] = None

    def to_dict(self):
        return {
            "address": self.address,
            "driver": self.driver,
            "vendor_id": self.vendor_id,
            "device_id": self.device_id,
            "vid": self.vid,
            "did": self.did,
            "vendor_name": self.vendor_name,
            "device_name": self.device_name,
            "pci_class": self.pci_class,
            "pci_subclass": self.pci_subclass,
            "pci_prog_if": self.pci_prog_if,
            "pci_subsystem_vendor_id": self.pci_subsystem_vendor_id,
            "pci_subsystem_id": self.pci_subsystem_id,
        }

    def friendly_name(self):
        return f"{self.vendor_id}:{self.device_id} ({self.vendor_name} {self.device_name})"

    def runtime_id(self) -> str:
        return f"pci-{self.address}"

    def persistent_id(self) -> str:
        return f"pci-{self.address}"

    def is_boot_device(self, _context):
        return False

def get_pci_info(device) -> PCIInfo:
    address  = device.sys_name
    driver  = device.driver
    pci_id = device.properties.get("PCI_ID")
    vid, did = pci_id.split(":")
    vendor_id = int(vid, 16)
    device_id = int(did, 16)
    vendor_name = device.properties.get("ID_VENDOR_FROM_DATABASE") or device.properties.get("ID_VENDOR")
    device_name = device.properties.get("ID_MODEL_FROM_DATABASE") or device.properties.get("ID_MODEL")
    class_hex = int(device.properties.get("PCI_CLASS"), 16)
    pci_class = (class_hex >> 16) & 0xFF
    pci_subclass = (class_hex >> 8) & 0xFF
    pci_prog_if = class_hex & 0xF
    pci_subsys_id = device.properties.get("PCI_SUBSYS_ID")
    pci_subsystem_vendor_id, pci_subsystem_id = pci_subsys_id.split(":")

    return PCIInfo(address, driver, vendor_id, device_id, vid, did, vendor_name, device_name, pci_class, pci_subclass, pci_prog_if, pci_subsystem_vendor_id, pci_subsystem_id)

def pci_device_by_address(app_context, address):
    for device in app_context.udev_context.list_devices(subsystem='pci'):
        pci_info = get_pci_info(device)
        if pci_info.address == address:
            return device
    return None

def pci_device_by_vid_did(app_context, vid, did):
    for device in app_context.udev_context.list_devices(subsystem='pci'):
        pci_info = get_pci_info(device)
        vid_match = pci_info.vendor_id and vid and pci_info.vendor_id == vid
        did_match = pci_info.device_id and did and pci_info.device_id == did
        if vid_match and did_match:
            return device
    return None

from typing import NamedTuple, Optional
import logging
import time
import os
from pathlib import Path

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
        return f"{self.vid}:{self.did} ({self.vendor_name} {self.device_name})"

    def runtime_id(self) -> str:
        return f"pci-{self.address}"

    def persistent_id(self) -> str:
        return f"pci-{self.address}"

    def is_boot_device(self, _context):
        return False

def get_pci_info(device) -> PCIInfo:
    address = device.sys_name
    driver = device.driver
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

def pci_info_by_address(app_context, address):
    for device in app_context.udev_context.list_devices(subsystem='pci'):
        pci_info = get_pci_info(device)
        if pci_info.address == address:
            return pci_info
    return None

def pci_info_by_vid_did(app_context, vid, did):
    for device in app_context.udev_context.list_devices(subsystem='pci'):
        pci_info = get_pci_info(device)
        vid_match = pci_info.vendor_id and vid and pci_info.vendor_id == vid
        did_match = pci_info.device_id and did and pci_info.device_id == did
        if vid_match and did_match:
            return pci_info
    return None

def _get_pci_driver(pci_address):
    """Returns PCI device driver name."""

    path = f"/sys/bus/pci/devices/{pci_address}/driver"
    if os.path.islink(path):
        return os.path.basename(os.readlink(path))
    return None

def _bind_vfio_pci(pci_address):
    """Checks the driver assigned for the device and changes it to vfio-pci."""

    device_path = f"/sys/bus/pci/devices/{pci_address}"

    driver = _get_pci_driver(pci_address)
    if driver == "vfio-pci":
        return

    # Unbind current driver
    if driver:
        logger.info("Device %s uses driver %s", pci_address, driver)
        for _ in range(1, 5):
            try:
                with open(f"{device_path}/driver/unbind", "w", encoding="utf-8") as f:
                    f.write(pci_address)
                logger.debug("Successfully unbound %s driver from %s", driver, device_path)
                break
            except OSError as e:
                logger.warning("Failed to unbind %s driver from %s: %s", driver, device_path, e)
            time.sleep(1)
        else:
            logger.error("Failed to unbind %s from %s after 5 attempts", driver, device_path)
    else:
        logger.debug("Device %s has no driver assigned", pci_address)

    # Bind vfio-pci driver
    with open(f"{device_path}/driver_override", "w", encoding="utf-8") as f:
        f.write("vfio-pci")

    with open("/sys/bus/pci/drivers_probe", "w", encoding="utf-8") as f:
        f.write(pci_address)

    logger.debug("Successfully bound vfio-pci driver for %s", pci_address)

def get_iommu_group_devices(pci_address):
    device_path = Path(f"/sys/bus/pci/devices/{pci_address}")
    if not device_path.exists():
        logger.error("Device path %s does not exist", device_path)
        return []

    # Wait for IOMMU group to appear
    for _ in range(1, 5):
        iommu_group = device_path / "iommu_group"
        if not iommu_group.exists():
            logger.warning("IOMMU group does not exist")
            time.sleep(0.1)
        else:
            iommu_group_path = iommu_group.resolve()
            logger.debug("IOMMU group: %s", iommu_group_path.name)

            # List all devices in the IOMMU group
            devices_dir = iommu_group_path / "devices"
            if devices_dir.exists():
                devices = sorted(os.listdir(devices_dir))
                return devices
            break
    return []

def setup_vfio(pci_info):
    """Checks PCI device IOMMU group and sets vfio-pci driver for all devices."""

    logger.debug("Setting up vfio for %s", pci_info.address)
    try:
        devices = get_iommu_group_devices(pci_info.address)
        # List all devices in the IOMMU group and setup vfio-pci driver for them:
        logger.debug("Devices in the group:")
        for dev in devices:
            logger.debug(" - %s", dev)
            _bind_vfio_pci(dev)

    except OSError as e:
        logger.error("Failed to setup VFIO for %s: %s", pci_info.address, e)

def check_vfio():
    if not os.path.exists("/sys/module/vfio_pci"):
        logger.warning("vfio-pci is not loaded")

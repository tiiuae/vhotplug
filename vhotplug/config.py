import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from vhotplug.evdev import EvdevInfo
from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo, parse_usb_interfaces

logger = logging.getLogger("vhotplug")


@dataclass
class PassthroughInfo:
    target_vm: str | None
    allowed_vms: list[str] | None
    skip_on_suspend: bool = False
    pci_iommu_add_all: bool = False
    pci_iommu_skip_if_shared: bool = False
    order: int = 0


class Config:
    def __init__(self, path: str) -> None:
        self.path = path
        self.config = self.load()

    def load(self) -> dict[str, Any]:
        with open(self.path, encoding="utf-8") as file:
            result: dict[str, Any] = json.load(file)
            return result

    def _disabled(self, node: dict[str, Any], default: bool = False) -> bool:
        if "disable" in node:
            return bool(node["disable"])
        if "enable" in node:
            return not bool(node["enable"])
        return default

    def _enabled(self, node: dict[str, Any], default: bool = True) -> bool:
        if "enable" in node:
            return bool(node["enable"])
        if "disable" in node:
            return not bool(node["disable"])
        return default

    def _hex_to_int(self, s: str | None) -> int | None:
        try:
            return int(s, 16) if s else None
        except (ValueError, TypeError):
            return None

    def _match_usb(self, usb_info: USBInfo, usb_rule: dict[str, Any]) -> bool:
        if self._disabled(usb_rule):
            return False

        rule_description = usb_rule.get("description", "")
        logger.debug("Rule: %s", rule_description)

        # Match by VID/PID
        rule_vid = usb_rule.get("vendorId")
        rule_pid = usb_rule.get("productId")
        logger.debug(
            "Checking %s:%s against %s:%s",
            usb_info.vid,
            usb_info.pid,
            rule_vid,
            rule_pid,
        )
        vid_match = rule_vid and usb_info.vid and usb_info.vid.casefold() == rule_vid.casefold()
        pid_match = rule_pid and usb_info.pid and usb_info.pid.casefold() == rule_pid.casefold()
        if vid_match and pid_match:
            logger.debug("Match by vendor id / product id, description: %s", rule_description)
            return True

        # Match by vendor name / product name
        rule_vname = usb_rule.get("vendorName")
        rule_pname = usb_rule.get("productName")
        logger.debug(
            "Checking %s:%s against %s:%s",
            usb_info.vendor_name,
            usb_info.product_name,
            rule_vname,
            rule_pname,
        )
        vname_match = rule_vname and re.match(rule_vname, usb_info.vendor_name or "", re.IGNORECASE)
        pname_match = rule_pname and re.match(rule_pname, usb_info.product_name or "", re.IGNORECASE)
        if vname_match or pname_match:
            logger.debug("Match by vendor name / product name, description: %s", rule_description)
            return True

        # Match by bus/port
        rule_bus = usb_rule.get("bus")
        rule_port = usb_rule.get("port")
        logger.debug(
            "Checking bus %s port %s against %s and %s",
            usb_info.busnum,
            usb_info.root_port,
            rule_bus,
            rule_port,
        )
        bus_match = rule_bus and usb_info.busnum and usb_info.busnum == rule_bus
        port_match = rule_port and usb_info.root_port and usb_info.root_port == rule_port
        if bus_match and port_match:
            logger.debug("Match by bus / port, description: %s", rule_description)
            return True

        # Match by device class, subclass and protocol
        rule_device_class = usb_rule.get("deviceClass")
        rule_device_subclass = usb_rule.get("deviceSubclass")
        rule_device_protocol = usb_rule.get("deviceProtocol")
        logger.debug(
            "Checking USB class %s, subclass %s, protocol %s",
            usb_info.device_class,
            usb_info.device_subclass,
            usb_info.device_protocol,
        )
        if rule_device_class and rule_device_class == usb_info.device_class:
            subclass_match = not rule_device_subclass or rule_device_subclass == usb_info.device_subclass
            protocol_match = not rule_device_protocol or rule_device_protocol == usb_info.device_protocol
            if subclass_match and protocol_match:
                logger.debug("Match by USB device class, description: %s", rule_description)
                return True

        # Match by interface class, subclass and protocol
        rule_interface_class = usb_rule.get("interfaceClass")
        rule_interface_subclass = usb_rule.get("interfaceSubclass")
        rule_interface_protocol = usb_rule.get("interfaceProtocol")
        logger.debug(
            "Checking rule interface class %s, subclass %s, protocol %s",
            rule_interface_class,
            rule_interface_subclass,
            rule_interface_protocol,
        )
        usb_interfaces = parse_usb_interfaces(usb_info.interfaces)
        for interface in usb_interfaces:
            interface_class = interface["class"]
            interface_subclass = interface["subclass"]
            interface_protocol = interface["protocol"]
            logger.debug(
                "Checking USB interface class %s, subclass %s, protocol %s",
                interface_class,
                interface_subclass,
                interface_protocol,
            )
            if rule_interface_class and rule_interface_class == interface_class:
                subclass_match = not rule_interface_subclass or rule_interface_subclass == interface_subclass
                protocol_match = not rule_interface_protocol or rule_interface_protocol == interface_protocol
                if subclass_match and protocol_match:
                    logger.debug(
                        "Match by USB interface class, description: %s",
                        rule_description,
                    )
                    return True

        return False

    def _match_pci(self, pci_info: PCIInfo, rule: dict[str, Any]) -> bool:
        if self._disabled(rule):
            return False

        rule_description = rule.get("description", "")
        logger.debug("Rule: %s", rule_description)

        # Match by address
        rule_address = rule.get("address")
        logger.debug("Checking %s against %s", pci_info.address, rule_address)
        address_match = rule_address and pci_info.address and pci_info.address.casefold() == rule_address.casefold()
        if address_match:
            logger.debug("Match by address, description: %s", rule_description)
            return True

        # Match by VID/DID
        rule_vid = self._hex_to_int(rule.get("vendorId"))
        rule_did = self._hex_to_int(rule.get("deviceId"))
        if rule_vid and rule_did and pci_info.vendor_id and pci_info.device_id:
            logger.debug(
                "Checking %04x:%04x against %04x:%04x",
                pci_info.vendor_id,
                pci_info.device_id,
                rule_vid,
                rule_did,
            )
            if pci_info.vendor_id == rule_vid and pci_info.device_id == rule_did:
                logger.debug("Match by vendor id / device id, description: %s", rule_description)
                return True

        # Match by PCI class, subclass and programming interface
        rule_device_class = rule.get("deviceClass")
        rule_device_subclass = rule.get("deviceSubclass")
        rule_device_prog_if = rule.get("deviceProgIf")
        logger.debug(
            "Checking PCI class %s, subclass %s, prog if %s",
            pci_info.pci_class,
            pci_info.pci_subclass,
            pci_info.pci_prog_if,
        )
        if rule_device_class and rule_device_class == pci_info.pci_class:
            subclass_match = not rule_device_subclass or rule_device_subclass == pci_info.pci_subclass
            prog_if_match = not rule_device_prog_if or rule_device_prog_if == pci_info.pci_prog_if
            if subclass_match and prog_if_match:
                logger.debug("Match by PCI class, description: %s", rule_description)
                return True

        return False

    def _match_evdev(self, evdev_info: EvdevInfo, rule: dict[str, Any]) -> bool:
        if self._disabled(rule):
            return False

        rule_description = rule.get("description", "")
        logger.debug("Rule: %s", rule_description)

        # Match by name
        rule_name = rule.get("name")
        logger.debug("Checking %s against %s", evdev_info.name, rule_name)
        name_match = rule_name and re.match(rule_name, evdev_info.name or "", re.IGNORECASE)
        if name_match:
            logger.debug("Match by name, description: %s", rule_description)
            return True

        # Match by path tag
        rule_path_tag = rule.get("pathTag")
        logger.debug("Checking %s against %s", evdev_info.path_tag, rule_path_tag)
        path_tag_match = rule_path_tag and re.match(rule_path_tag, evdev_info.path_tag or "", re.IGNORECASE)
        if path_tag_match:
            logger.debug("Match by path tag, description: %s", rule_description)
            return True

        # Match by property
        rule_property = rule.get("property")
        rule_value = rule.get("value")
        logger.debug("Checking property %s value %s", rule_property, rule_value)
        if rule_property and rule_value:
            value = evdev_info.properties.get(rule_property)
            if value and value.casefold() == rule_value.casefold():
                logger.debug("Match by property, description: %s", rule_description)
                return True

        return False

    def vm_for_usb_device(self, usb_info: USBInfo) -> PassthroughInfo | None:
        try:
            dev_name = usb_info.friendly_name()
            logger.debug("Searching for a VM for %s", dev_name)

            for rule in self.config.get("usbPassthrough", []):
                rule_name = rule.get("description")
                if self._disabled(rule):
                    continue

                found = False
                for allow in rule.get("allow", []):
                    if self._match_usb(usb_info, allow):
                        found = True
                        break

                for deny in rule.get("deny", []):
                    if self._match_usb(usb_info, deny):
                        found = False
                        break

                if found:
                    target_vm = rule.get("targetVm")
                    allowed_vms = rule.get("allowedVms")
                    if target_vm:
                        logger.debug("Found VM %s for %s", target_vm, dev_name)
                    elif allowed_vms:
                        logger.debug("Found allowed VMs %s for %s", allowed_vms, dev_name)
                    else:
                        logger.error("No target VM or allowed VMs defined for rule %s", rule_name)
                        continue

                    skip_on_suspend = rule.get("skipOnSuspend", False)
                    return PassthroughInfo(target_vm, allowed_vms, skip_on_suspend)

        except (AttributeError, TypeError):
            logger.exception("Failed to find VM for USB device in the configuration file")
        return None

    def vm_for_pci_device(self, pci_info: PCIInfo) -> PassthroughInfo | None:
        try:
            dev_name = pci_info.friendly_name()
            logger.debug("Searching for a VM for %s", dev_name)

            order = 0
            for rule in self.config.get("pciPassthrough", []):
                rule_name = rule.get("description", "")
                if self._disabled(rule):
                    continue

                found = False
                for allow in rule.get("allow", []):
                    order = order + 1
                    if self._match_pci(pci_info, allow):
                        found = True
                        break

                for deny in rule.get("deny", []):
                    if self._match_pci(pci_info, deny):
                        found = False
                        break

                if found:
                    target_vm = rule.get("targetVm")
                    if not target_vm:
                        logger.error("No target VM defined for rule %s", rule_name)
                        continue

                    logger.debug("Found VM %s for %s", target_vm, dev_name)
                    skip_on_suspend = rule.get("skipOnSuspend", False)
                    pci_iommu_add_all = rule.get("pciIommuAddAll", False)
                    pci_iommu_skip_if_shared = rule.get("pciIommuSkipIfShared", False)
                    return PassthroughInfo(
                        target_vm, [], skip_on_suspend, pci_iommu_add_all, pci_iommu_skip_if_shared, order
                    )

        except (AttributeError, TypeError):
            logger.exception("Failed to find VM for PCI device in the configuration file")
        return None

    def vm_for_evdev_device(self, evdev_info: EvdevInfo) -> PassthroughInfo | None:
        try:
            dev_name = evdev_info.friendly_name()
            logger.debug("Searching for a VM for %s", dev_name)

            for rule in self.config.get("evdevPassthrough", []):
                rule_name = rule.get("description", "")
                if self._disabled(rule):
                    continue

                found = False
                for allow in rule.get("allow", []):
                    if self._match_evdev(evdev_info, allow):
                        found = True
                        break

                for deny in rule.get("deny", []):
                    if self._match_evdev(evdev_info, deny):
                        found = False
                        break

                if found:
                    target_vm = rule.get("targetVm")
                    if not target_vm:
                        logger.error("No target VM defined for rule %s", rule_name)
                        continue

                    logger.debug("Found VM %s for %s", target_vm, dev_name)
                    return PassthroughInfo(target_vm, [])

        except (AttributeError, TypeError):
            logger.exception("Failed to find VM for evdev device in the configuration file")
        return None

    def vm_for_device(self, dev_info: USBInfo | PCIInfo | EvdevInfo) -> PassthroughInfo | None:
        if isinstance(dev_info, USBInfo):
            return self.vm_for_usb_device(dev_info)
        if isinstance(dev_info, PCIInfo):
            return self.vm_for_pci_device(dev_info)
        return self.vm_for_evdev_device(dev_info)

    def get_all_vms(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self.config.get("vms", [])
        return result

    def get_vm(self, vm_name: str) -> dict[str, Any] | None:
        for vm in self.config.get("vms", []):
            if vm.get("name") == vm_name:
                return dict(vm)
        return None

    def get_vm_by_socket(self, socket: str) -> dict[str, Any] | None:
        for vm in self.config.get("vms", []):
            if vm.get("socket") == socket:
                return dict(vm)
        return None

    def api_enabled(self) -> bool:
        return bool(self._enabled(self.config.get("general", {}).get("api", {})))

    def persistency_enabled(self) -> bool:
        result: bool = self.config.get("general", {}).get("persistency", True)
        return result

    def state_path(self) -> str:
        result: str = self.config.get("general", {}).get("statePath", "/var/lib/vhotplug/vhotplug.state")
        return result

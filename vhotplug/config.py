import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional
from vhotplug.usb import parse_usb_interfaces, USBInfo

logger = logging.getLogger("vhotplug")

@dataclass
class PassthroughInfo:
    target_vm: Optional[str]
    allowed_vms: Optional[List[str]]
    skip_on_suspend: bool = False

class Config:
    def __init__(self, path):
        self.path = path
        self.config = self.load()

    def load(self):
        with open(self.path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _disabled(self, node, default=False):
        if "disable" in node:
            return bool(node["disable"])
        if "enable" in node:
            return not bool(node["enable"])
        return default

    def _enabled(self, node, default=True):
        if "enable" in node:
            return bool(node["enable"])
        if "disable" in node:
            return not bool(node["disable"])
        return default

    # pylint: disable = too-many-locals, too-many-return-statements
    def _match_usb(self, usb_info, usb_rule):
        if self._disabled(usb_rule):
            return False

        rule_description = usb_rule.get("description")
        logger.debug("Rule: %s", rule_description)

        # Match by VID/PID
        rule_vid = usb_rule.get("vendorId")
        rule_pid = usb_rule.get("productId")
        logger.debug("Checking %s:%s against %s:%s", usb_info.vid, usb_info.pid, rule_vid, rule_pid)
        vid_match = rule_vid and usb_info.vid and usb_info.vid.casefold() == rule_vid.casefold()
        pid_match = rule_pid and usb_info.pid and usb_info.pid.casefold() == rule_pid.casefold()
        if vid_match and pid_match:
            logger.debug("Match by vendor id / product id, description: %s", rule_description)
            return True

        # Match by vendor name / product name
        rule_vname = usb_rule.get("vendorName")
        rule_pname = usb_rule.get("productName")
        logger.debug("Checking %s:%s against %s:%s", usb_info.vendor_name, usb_info.product_name, rule_vname, rule_pname)
        vname_match = rule_vname and re.match(rule_vname, usb_info.vendor_name or "", re.IGNORECASE)
        pname_match = rule_pname and re.match(rule_pname, usb_info.product_name or "", re.IGNORECASE)
        if vname_match or pname_match:
            logger.debug("Match by vendor name / product name, description: %s", rule_description)
            return True

        # Match by bus/port
        rule_bus = usb_rule.get("bus")
        rule_port = usb_rule.get("port")
        logger.debug("Checking bus %s port %s against %s and %s", usb_info.busnum, usb_info.root_port, rule_bus, rule_port)
        bus_match = rule_bus and usb_info.busnum and usb_info.busnum == rule_bus
        port_match = rule_port and usb_info.root_port and usb_info.root_port == rule_port
        if bus_match and port_match:
            logger.debug("Match by bus / port, description: %s", rule_description)
            return True

        # Match by device class, subclass and protocol
        rule_device_class = usb_rule.get("deviceClass")
        rule_device_subclass = usb_rule.get("deviceSubclass")
        rule_device_protocol = usb_rule.get("deviceProtocol")
        logger.debug("Checking USB class %s, subclass %s, protocol %s", usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol)
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
        logger.debug("Checking rule interface class %s, subclass %s, protocol %s", rule_interface_class, rule_interface_subclass, rule_interface_protocol)
        usb_interfaces = parse_usb_interfaces(usb_info.interfaces)
        for interface in usb_interfaces:
            interface_class = interface["class"]
            interface_subclass = interface["subclass"]
            interface_protocol = interface["protocol"]
            logger.debug("Checking USB interface class %s, subclass %s, protocol %s", interface_class, interface_subclass, interface_protocol)
            if rule_interface_class and rule_interface_class == interface_class:
                subclass_match = not rule_interface_subclass or rule_interface_subclass == interface_subclass
                protocol_match = not rule_interface_protocol or rule_interface_protocol == interface_protocol
                if subclass_match and protocol_match:
                    logger.debug("Match by USB interface class, description: %s", rule_description)
                    return True

        return False

    def _match_pci(self, pci_info, rule):
        if self._disabled(rule):
            return False

        rule_description = rule.get("description")
        logger.debug("Rule: %s", rule_description)

        # Match by address
        rule_address = rule.get("address")
        logger.debug("Checking %s against %s", pci_info.address, rule_address)
        address_match = rule_address and pci_info.address and pci_info.address.casefold() == rule_address.casefold()
        if address_match:
            logger.debug("Match by address, description: %s", rule_description)
            return True

        # Match by VID/DID
        rule_vid = int(rule.get("vendorId"), 16) if "vendorId" in rule else None
        rule_did = int(rule.get("deviceId"), 16) if "deviceId" in rule else None
        if rule_vid and rule_did and pci_info.vendor_id and pci_info.device_id:
            logger.debug("Checking %04x:%04x against %04x:%04x", pci_info.vendor_id, pci_info.device_id, rule_vid, rule_did)
            if pci_info.vendor_id == rule_vid and pci_info.device_id == rule_did:
                logger.debug("Match by vendor id / device id, description: %s", rule_description)
                return True

        # Match by PCI class, subclass and programming interface
        rule_device_class = rule.get("deviceClass")
        rule_device_subclass = rule.get("deviceSubclass")
        rule_device_prog_if = rule.get("deviceProgIf")
        logger.debug("Checking PCI class %s, subclass %s, prog if %s", pci_info.pci_class, pci_info.pci_subclass, pci_info.pci_prog_if)
        if rule_device_class and rule_device_class == pci_info.pci_class:
            subclass_match = not rule_device_subclass or rule_device_subclass == pci_info.pci_subclass
            prog_if_match = not rule_device_prog_if or rule_device_prog_if == pci_info.pci_prog_if
            if subclass_match and prog_if_match:
                logger.debug("Match by PCI class, description: %s", rule_description)
                return True

        return False

    def vm_for_device(self, dev_info):
        try:
            dev_name = dev_info.friendly_name()
            logger.debug("Searching for a VM for %s", dev_name)

            is_usb_dev = isinstance(dev_info, USBInfo)
            node_name = "usbPassthrough" if is_usb_dev else "pciPassthrough"
            match = self._match_usb if is_usb_dev else self._match_pci

            for rule in self.config.get(node_name, []):
                rule_name = rule.get("description")
                if self._disabled(rule):
                    continue

                found = False
                for allow in rule.get("allow", []):
                    if match(dev_info, allow):
                        found = True
                        break

                for deny in rule.get("deny", []):
                    if match(dev_info, deny):
                        found = False
                        break

                if found:
                    target_vm = rule.get("targetVm")
                    allowed_vms = rule.get("allowedVms")
                    skip_on_suspend = rule.get("skipOnSuspend", False)
                    if target_vm:
                        logger.debug("Found VM %s for %s", target_vm, dev_name)
                    elif allowed_vms:
                        logger.debug("Found allowed VMs %s for %s", allowed_vms, dev_name)
                    else:
                        logger.error("No target VM or allowed VMs defined for rule %s", rule_name)
                    return PassthroughInfo(target_vm, allowed_vms, skip_on_suspend)

        except (AttributeError, TypeError) as e:
            logger.error("Failed to find VM for device in the configuration file: %s", e)
        return None

    def vm_for_evdev_devices(self):
        try:
            evdev = self.config.get("evdevPassthrough")
            if evdev and self._disabled(evdev) is not True:
                vm_name = evdev.get("targetVm")
                logger.debug("Found VM %s for evdev passthrough", vm_name)
                bus_prefix = evdev.get("pcieBusPrefix")
                return self.get_vm(vm_name), bus_prefix
        except (AttributeError, TypeError) as e:
            logger.error("Failed to find VM for evdev device in the configuration file: %s", e)
        return None, None

    def get_all_vms(self):
        return self.config.get("vms", [])

    def get_vm(self, vm_name):
        for vm in self.config.get("vms", []):
            if vm.get("name") == vm_name:
                return vm
        return None

    def get_vm_by_socket(self, socket):
        for vm in self.config.get("vms", []):
            if vm.get("socket") == socket:
                return vm
        return None

    def api_enabled(self):
        return self._enabled(self.config.get("general", {}).get("api", {}))

    def persistency_enabled(self):
        return self.config.get("general", {}).get("persistency", True)

    def state_path(self):
        return self.config.get("general", {}).get("statePath", "/var/lib/vhotplug/vhotplug.state")

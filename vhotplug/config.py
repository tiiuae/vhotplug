import json
import logging
import re
from vhotplug.usb import parse_usb_interfaces

logger = logging.getLogger("vhotplug")

class Config:
    def __init__(self, path):
        self.path = path
        self.config = self.load()

    def load(self):
        with open(self.path, "r", encoding="utf-8") as file:
            return json.load(file)

    # pylint: disable = too-many-locals
    def match(self, usb_info, usb_rule):
        if usb_rule.get("disable") is True:
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

        # Match by device class, subclass and protocol
        rule_device_class = usb_rule.get("deviceClass")
        rule_device_subclass = usb_rule.get("deviceSubclass")
        rule_device_protocol = usb_rule.get("deviceProtocol")
        logger.debug("Checking device class %s, subclass %s, protocol %s", usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol)
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
            logger.debug("Checking usb interface class %s, subclass %s, protocol %s", interface_class, interface_subclass, interface_protocol)
            if rule_interface_class and rule_interface_class == interface_class:
                subclass_match = not rule_interface_subclass or rule_interface_subclass == interface_subclass
                protocol_match = not rule_interface_protocol or rule_interface_protocol == interface_protocol
                if subclass_match and protocol_match:
                    logger.debug("Match by USB interface class, description: %s", rule_description)
                    return True
        return False

    def vm_for_usb_device(self, usb_info):
        try:
            usb_dev_name = f"{usb_info.vid}:{usb_info.pid} ({usb_info.vendor_name}:{usb_info.product_name})"
            logger.debug("Searching for a VM for %s", usb_dev_name)

            for usb_rule in self.config.get("usbPassthrough", []):
                rule_name = usb_rule.get("description")
                if usb_rule.get("disable") is True:
                    continue

                found = False
                for allow in usb_rule.get("allow", []):
                    if self.match(usb_info, allow):
                        found = True
                        break

                for deny in usb_rule.get("deny", []):
                    if self.match(usb_info, deny):
                        found = False
                        break

                if found:
                    target_vm = usb_rule.get("targetVm")
                    allowed_vms = usb_rule.get("allowedVms")
                    if target_vm:
                        logger.info("Found VM %s for %s", target_vm, usb_dev_name)
                    elif allowed_vms:
                        logger.info("Found allowed VMs %s for %s", allowed_vms, usb_dev_name)
                    else:
                        logger.error("No target VM or allowed VMs defined for rule %s", rule_name)
                    return (target_vm, allowed_vms)

        except (AttributeError, TypeError) as e:
            logger.error("Failed to find VM for USB device in the configuration file: %s", e)
        return None

    def vm_for_evdev_devices(self):
        try:
            evdev = self.config.get("evdevPassthrough")
            if evdev:
                vm_name = evdev.get("targetVm")
                disable = evdev.get("disable", False)
                if disable is not True:
                    logger.debug("Found VM %s for evdev passthrough", vm_name)
                    bus_prefix = evdev.get("pcieBusPrefix")
                    return self.get_vm(vm_name), bus_prefix
        except (AttributeError, TypeError) as e:
            logger.error("Failed to find VM for evdev device in the configuration file: %s", e)
        return None

    def get_all_vms(self):
        return self.config.get("vms", [])

    def get_vm(self, vm_name):
        for vm in self.config.get("vms", []):
            if vm.get("name") == vm_name:
                return vm
        return None

    def api_enabled(self):
        return self.config.get("general", {}).get("api", {}).get("enable", False)

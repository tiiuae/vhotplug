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
        logger.debug("Rule %s", rule_description)

        # Find a VM by VID/PID
        rule_vid = usb_rule.get("vendorId")
        rule_pid = usb_rule.get("productId")
        logger.debug("Checking %s:%s against %s:%s", usb_info.vid, usb_info.pid, rule_vid, rule_pid)
        vid_match = rule_vid and usb_info.vid and usb_info.vid.casefold() == rule_vid.casefold()
        pid_match = rule_pid and usb_info.pid and usb_info.pid.casefold() == rule_pid.casefold()
        if vid_match and pid_match:
            logger.info("Match by vendor id / product id, description: %s", rule_description)
            return True

        # Find a VM by vendor name / product name
        rule_vname = usb_rule.get("vendorName")
        rule_pname = usb_rule.get("productName")
        logger.debug("Checking %s:%s against %s:%s", usb_info.vendor_name, usb_info.product_name, rule_vname, rule_pname)
        vname_match = rule_vname and re.match(rule_vname, usb_info.vendor_name or "", re.IGNORECASE)
        pname_match = rule_pname and re.match(rule_pname, usb_info.product_name or "", re.IGNORECASE)
        if vname_match or pname_match:
            logger.info("Match by vendor name / product name, description: %s", rule_description)
            return True

        # Find a VM by device class, subclass and protocol
        rule_device_class = usb_rule.get("deviceClass")
        rule_device_subclass = usb_rule.get("deviceSubclass")
        rule_device_protocol = usb_rule.get("deviceProtocol")
        logger.debug("Checking device class %s, subclass %s, protocol %s", usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol)
        if rule_device_class and rule_device_class == usb_info.device_class:
            subclass_match = not rule_device_subclass or rule_device_subclass == usb_info.device_subclass
            protocol_match = not rule_device_protocol or rule_device_protocol == usb_info.device_protocol
            if subclass_match and protocol_match:
                logger.info("Match by USB device class, description: %s", rule_description)
                return True

        # Find a VM by interface class, subclass and protocol
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
                    logger.info("Match by USB interface class, description: %s", rule_description)
                    return True
        return False

    # pylint: disable = too-many-nested-blocks
    def vm_for_usb_device(self, usb_info):
        try:
            usb_dev_name = f"{usb_info.vid}:{usb_info.pid} ({usb_info.vendor_name}:{usb_info.product_name})"
            logger.debug("Searching for a VM for %s", usb_dev_name)
            # Enumerate all virtual machines and check passthrough rules
            for vm in self.config.get("vms", []):
                vm_name = vm.get("name")
                for usb_rule in vm.get("usbPassthrough", []):
                    if self.match(usb_info, usb_rule):
                        logger.info("Found VM %s for %s", vm_name, usb_dev_name)
                        # Found a VM, check ignored devices
                        ignore = False
                        for usb_rule_ignore in usb_rule.get("ignore", []):
                            if self.match(usb_info, usb_rule_ignore):
                                logger.info("Device %s is ignored", usb_dev_name)
                                ignore = True
                                break
                        if not ignore:
                            return vm
        except (AttributeError, TypeError, ValueError) as e:
            logger.error("Failed to find VM for USB device in the configuration file: %s", e)
        return None

    def vm_for_evdev_devices(self):
        try:
            logger.debug("Searching for a VM for evdev passthrough")
            for vm in self.config.get("vms", []):
                vm_name = vm.get("name")
                evdev = vm.get("evdevPassthrough")
                if evdev:
                    enable = evdev.get("enable")
                    if enable:
                        logger.debug("Found VM %s for evdev passthrough", vm_name)
                        bus_prefix = evdev.get("pcieBusPrefix")
                        return vm, bus_prefix
        except (AttributeError, TypeError, ValueError) as e:
            logger.error("Failed to find VM for evdev device in the configuration file: %s", e)
        return None

    def get_all_vms(self):
        return self.config.get("vms", [])

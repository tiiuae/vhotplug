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
        with open(self.path, 'r') as file:
            return json.load(file)

    def match(self, usb_info, usb_rule):
        if usb_rule.get("disable") == True:
            return False

        rule_description = usb_rule.get("description")
        logger.debug(f"Rule {rule_description}")

        # Find a VM by VID/PID
        rule_vid = usb_rule.get("vendorId")
        rule_pid = usb_rule.get("productId")
        logger.debug(f"Checking {usb_info.vid}:{usb_info.pid} against {rule_vid}:{rule_pid}")
        vidMatch = rule_vid and usb_info.vid and usb_info.vid.casefold() == rule_vid.casefold()
        pidMatch = rule_pid and usb_info.pid and usb_info.pid.casefold() == rule_pid.casefold()
        if vidMatch and pidMatch:
            logger.info(f"Match by vendor id / product id, description: {rule_description}")
            return True

        # Find a VM by vendor name / product name
        rule_vname = usb_rule.get("vendorName")
        rule_pname = usb_rule.get("productName")
        logger.debug(f"Checking {usb_info.vendor_name}:{usb_info.product_name} against {rule_vname}:{rule_pname}")
        vnameMatch = rule_vname and re.match(rule_vname, usb_info.vendor_name or "", re.IGNORECASE)
        pnameMatch = rule_pname and re.match(rule_pname, usb_info.product_name or "", re.IGNORECASE)
        if vnameMatch or pnameMatch:
            logger.info(f"Match by vendor name / product name, description: {rule_description}")
            return True

        # Find a VM by device class, subclass and protocol
        rule_device_class = usb_rule.get("deviceClass")
        rule_device_subclass = usb_rule.get("deviceSubclass")
        rule_device_protocol = usb_rule.get("deviceProtocol")
        logger.debug(f"Checking device class {usb_info.device_class}, subclass {usb_info.device_subclass}, protocol {usb_info.device_protocol}")
        if rule_device_class and rule_device_class == usb_info.device_class:
            subclassMatch = not rule_device_subclass or rule_device_subclass == usb_info.device_subclass
            protocolMatch = not rule_device_protocol or rule_device_protocol == usb_info.device_protocol
            if subclassMatch and protocolMatch:
                logger.info(f"Match by USB device class, description: {rule_description}")
                return True

        # Find a VM by interface class, subclass and protocol
        rule_interface_class = usb_rule.get("interfaceClass")
        rule_interface_subclass = usb_rule.get("interfaceSubclass")
        rule_interface_protocol = usb_rule.get("interfaceProtocol")
        logger.debug(f"Checking rule interface class {rule_interface_class}, subclass {rule_interface_subclass}, protocol {rule_interface_protocol}")
        usb_interfaces = parse_usb_interfaces(usb_info.interfaces)
        for interface in usb_interfaces:
            interface_class = interface["class"]
            interface_subclass = interface["subclass"]
            interface_protocol = interface["protocol"]
            logger.debug(f"Checking usb interface class {interface_class}, subclass {interface_subclass}, protocol {interface_protocol}")
            if rule_interface_class and rule_interface_class == interface_class:
                subclassMatch = not rule_interface_subclass or rule_interface_subclass == interface_subclass
                protocolMatch = not rule_interface_protocol or rule_interface_protocol == interface_protocol
                if subclassMatch and protocolMatch:
                    logger.info(f"Match by USB interface class, description: {rule_description}")
                    return True
        return False

    def vm_for_usb_device(self, usb_info):
        try:
            usb_dev_name = f"{usb_info.vid}:{usb_info.pid} ({usb_info.vendor_name}:{usb_info.product_name})"
            logger.debug(f"Searching for a VM for {usb_dev_name}")
            # Enumerate all virtual machines and check passthrough rules
            for vm in self.config.get("vms", []):
                vm_name = vm.get("name")
                for usb_rule in vm.get("usbPassthrough", []):
                    if self.match(usb_info, usb_rule):
                        logger.info(f"Found VM {vm_name} for {usb_dev_name}")
                        # Found a VM, check ignored devices
                        ignore = False
                        for usb_rule_ignore in usb_rule.get("ignore", []):
                            if self.match(usb_info, usb_rule_ignore):
                                logger.info(f"Device {usb_dev_name} is ignored")
                                ignore = True
                                break
                        if not ignore:
                            return vm
        except Exception as e:
            logger.error(f"Failed to find VM for USB device in the configuration file: {e}")
        return None

    def vm_for_evdev_devices(self):
        try:
            logger.debug(f"Searching for a VM for evdev passthrough")
            for vm in self.config.get("vms", []):
                vm_name = vm.get("name")
                evdev = vm.get("evdevPassthrough")
                if evdev:
                    enable = evdev.get("enable")
                    if enable:
                        logger.debug(f"Found VM {vm_name} for evdev passthrough")
                        bus_prefix = evdev.get("pcieBusPrefix")
                        return vm, bus_prefix
        except Exception as e:
            logger.error(f"Failed to find VM for evdev device in the configuration file: {e}")
        return None

    def get_all_vms(self):
        return self.config.get("vms", [])

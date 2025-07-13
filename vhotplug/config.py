import json
import logging
import re

logger = logging.getLogger("vhotplug")

class Config:
    def __init__(self, path):
        self.path = path
        self.config = self.load()

    def load(self):
        with open(self.path, 'r') as file:
            return json.load(file)

    def vm_for_usb_device(self, vid, pid, vendor_name, product_name, interfaces):
        from vhotplug.device import parse_usb_interfaces
        try:
            logger.debug(f"Searching for a VM for {vid}:{pid}, {vendor_name}:{product_name}")
            for vm in self.config.get("vms", []):
                vm_name = vm.get("name")
                for usb in vm.get("usbPassthrough", []):
                    matches = False

                    if usb.get("disable") == True:
                        continue

                    # Find a VM by VID/PID
                    usb_vid = usb.get("vendorId")
                    usb_pid = usb.get("productId")
                    usb_description = usb.get("description")
                    logger.debug(f"Rule {usb_description}")
                    logger.debug(f"Checking {vid}:{pid} against {usb_vid}:{usb_pid}")
                    vidMatch = usb_vid and vid.casefold() == usb_vid.casefold()
                    pidMatch = usb_pid and pid.casefold() == usb_pid.casefold()
                    if vidMatch and pidMatch:
                        logger.info(f"Found VM {vm_name} by vendor id / product id, description: {usb_description}")
                        matches = True

                    # Find a VM by vendor name / product name
                    if not matches:
                        usb_vname = usb.get("vendorName")
                        usb_pname = usb.get("productName")
                        logger.debug(f"Checking {vendor_name}:{product_name} against {usb_vname}:{usb_pname}")
                        vnameMatch = usb_vname and re.match(usb_vname, vendor_name, re.IGNORECASE)
                        pnameMatch = usb_pname and re.match(usb_pname, product_name, re.IGNORECASE)
                        if vnameMatch or pnameMatch:
                            logger.info(f"Found VM {vm_name} by vendor name / product name, description: {usb_description}")
                            matches = True

                    # Find a VM by interface class, subclass and protocol
                    if not matches:
                        usb_class = usb.get("class")
                        usb_subclass = usb.get("subclass")
                        usb_protocol = usb.get("protocol")
                        usb_interfaces = parse_usb_interfaces(interfaces)
                        for interface in usb_interfaces:
                            interface_class = interface["class"]
                            interface_subclass = interface["subclass"]
                            interface_protocol = interface["protocol"]
                            logger.debug(f"Checking class {interface_class}, subclass {interface_subclass}, protocol: {interface_protocol}")
                            if usb_class and usb_class == interface_class:
                                subclassMatch = not usb_subclass or usb_subclass == interface_subclass
                                protocolMatch = not usb_protocol or usb_protocol == interface_protocol
                                if subclassMatch and protocolMatch:
                                    logger.info(f"Found VM {vm_name} by USB interface class, description: {usb_description}")
                                    matches = True
                                    break

                    # Check ignored devices
                    if matches:
                        ignore = False
                        for dev in usb.get("ignore", []):
                            if dev.get("disable") == True:
                                continue
                            ignore_vid = dev.get("vendorId")
                            ignore_pid = dev.get("productId")
                            ignore_description = dev.get("description")
                            if (vid and pid) and (vid.casefold() == ignore_vid.casefold()) and (pid.casefold() == ignore_pid.casefold()):
                                logger.info(f"Device {vid}:{pid} is ignored, description: {ignore_description}")
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

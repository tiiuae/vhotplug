import json
import logging

logger = logging.getLogger("vhotplug")

class Config:
    def __init__(self, path):
        self.path = path
        self.config = self.load()

    def load(self):
        with open(self.path, 'r') as file:
            return json.load(file)

    def parse_usb_interfaces(self, interfaces):
        result = []
        if interfaces:
            try:
                interfaces = interfaces.strip(':')
                for interface in interfaces.split(':'):
                    if len(interface) >= 6:
                        usb_class = interface[:2]
                        usb_subclass = interface[2:4]
                        usb_protocol = interface[4:6]
                        result.append({
                            "class": int(usb_class, 16),
                            "subclass": int(usb_subclass, 16),
                            "protocol": int(usb_protocol, 16)
                        })
            except Exception as e:
                logger.error(f"Failed to parse USB interfaces: {e}")
        return result

    def vm_for_usb_device(self, vid, pid, interfaces):
        if not vid or not pid:
            return None
        try:
            logger.debug(f"Searching for a VM for {vid}:{pid}")
            for vm in self.config.get("vms", []):
                vm_name = vm.get("name")
                # Find a VM by VID/PID
                for usb_device in vm.get("usbDevices", []):
                    usb_device_vid = usb_device.get("vid")
                    usb_device_pid = usb_device.get("pid")
                    logger.debug(f"Checking {usb_device_vid}:{usb_device_pid}")
                    if vid.casefold() == usb_device_vid.casefold() and pid.casefold() == usb_device_pid.casefold():
                        logger.info(f"Found VM {vm_name} for USB device by VID/PID")
                        return vm
                # Find a VM by interface class, subclass and protocol
                usb_interfaces = self.parse_usb_interfaces(interfaces)
                logger.debug(f"Searching for a VM by USB class")
                for usb_class in vm.get("usbClasses", []):
                    usb_class_class = usb_class.get("class")
                    usb_class_subclass = usb_class.get("subclass")
                    usb_class_protocol = usb_class.get("protocol")
                    if not usb_class_class:
                        logger.warning("Empty USB interface class in the configuration file")
                        continue
                    logger.debug(f"Checking class {usb_class_class}, subclass {usb_class_subclass}, protocol: {usb_class_protocol}")
                    for interface in usb_interfaces:
                        interface_class = interface["class"]
                        interface_subclass = interface["subclass"]
                        interface_protocol = interface["protocol"]
                        logger.debug(f"Interface class {interface_class}, subclass {interface_subclass}, protocol: {interface_protocol}")
                        if (usb_class_class == interface_class) and (usb_class_subclass == None or usb_class_subclass == interface_subclass) and \
                            (usb_class_protocol == None or usb_class_protocol == interface_protocol):
                            logger.info(f"Found VM {vm_name} for USB device by USB interface class")
                            # Check ignored devices
                            ignore_devices = usb_class.get("ignoreDevices", [])
                            for ignore_device in ignore_devices:
                                ignore_vid = ignore_device.get("vid")
                                ignore_pid = ignore_device.get("pid")
                                if vid.casefold() == ignore_vid.casefold() and pid.casefold() == ignore_pid.casefold():
                                    logger.info(f"Device {vid}:{pid} is ignored")
                                    return None
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

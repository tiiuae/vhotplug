from typing import NamedTuple, Optional, List
import logging
import psutil
import pyudev

logger = logging.getLogger("vhotplug")

class USBInfo(NamedTuple):
    device_node: Optional[str] = None

    vid: Optional[str] = None
    pid: Optional[str] = None
    vendor_name: Optional[str] = None
    product_name: Optional[str] = None
    interfaces: Optional[str] = None
    device_class: Optional[int] = None
    device_subclass: Optional[int] = None
    device_protocol: Optional[int] = None
    busnum: Optional[int] = None
    devnum: Optional[int] = None
    serial: Optional[str] = None
    ports: Optional[List[int]] = None
    sys_name: Optional[str] = None

    def to_dict(self):
        return {
            "device_node": self.device_node,
            "vid": self.vid,
            "pid": self.pid,
            "vendor_name": self.vendor_name,
            "product_name": self.product_name,
            "interfaces": self.interfaces,
            "device_class": self.device_class,
            "device_subclass": self.device_subclass,
            "device_protocol": self.device_protocol,
            "busnum": self.busnum,
            "devnum": self.devnum,
            "portnum": self.root_port,
            "serial": self.serial,
            "sys_name": self.sys_name
        }

    def friendly_name(self):
        if self.vid and self.pid:
            return f"{self.vid}:{self.pid} ({self.vendor_name} {self.product_name})"
        return self.device_node

    def runtime_id(self) -> str:
        return f"usb-{self.device_node}"

    def persistent_id(self) -> str:
        return f"usb-{self.vid}:{self.pid}:{self.serial}"

    @property
    def root_port(self):
        return self.ports[0] if self.ports else None # pylint: disable=unsubscriptable-object

    def is_boot_device(self, context):
        # Find device partitions
        for udevpart in context.list_devices(subsystem="block", DEVTYPE="partition"):
            parent = udevpart.find_parent("usb", "usb_device")
            if parent and parent.device_node == self.device_node:
                logger.debug("USB drive %s has partition %s", self.device_node, udevpart.device_node)
                # Find mountpoints
                partitions = psutil.disk_partitions(all=True)
                for part in partitions:
                    if part.device == udevpart.device_node:
                        logger.debug("Found mountpoint %s with filesystem %s", part.mountpoint, part.fstype)
                        logger.debug("Options: %s", part.opts)
                        if part.mountpoint == "/boot":
                            return True
        return False

def _bytes_to_int(data):
    if not data:
        return None
    try:
        return int(data.decode().strip(), 16)
    except ValueError:
        return None

def _get_ports(sys_name):
    try:
        parts = sys_name.split('-')
        #bus = int(parts[0])
        ports = [int(x) for x in parts[1].split('.')]
    except (IndexError, ValueError):
        ports = []
    return ports

def get_usb_info(device) -> USBInfo:
    device_node  = device.device_node
    vid = device.properties.get("ID_VENDOR_ID")
    pid = device.properties.get("ID_MODEL_ID")
    vendor_name = device.properties.get("ID_VENDOR_FROM_DATABASE") or device.properties.get("ID_VENDOR")
    product_name = device.properties.get("ID_MODEL_FROM_DATABASE") or device.properties.get("ID_MODEL")
    interfaces = device.properties.get("ID_USB_INTERFACES")
    device_class = _bytes_to_int(device.attributes.get("bDeviceClass"))
    device_subclass = _bytes_to_int(device.attributes.get("bDeviceSubClass"))
    device_protocol = _bytes_to_int(device.attributes.get("bDeviceProtocol"))
    busnum = int(device.properties.get("BUSNUM"))
    devnum = int(device.properties.get("DEVNUM"))
    serial = device.properties.get("ID_SERIAL_SHORT")
    ports = _get_ports(device.sys_name)
    sys_name = device.sys_name

    return USBInfo(device_node, vid, pid, vendor_name, product_name, interfaces, device_class, device_subclass, device_protocol, busnum, devnum, serial, ports, sys_name)

def parse_usb_interfaces(interfaces):
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
        except (ValueError, TypeError) as e:
            logger.error("Failed to parse USB interfaces: %s", e)
    return result

def is_usb_hub(interfaces):
    usb_interfaces = parse_usb_interfaces(interfaces)
    for interface in usb_interfaces:
        interface_class = interface["class"]
        if interface_class == 9:
            return True
    return False

def is_usb_device(device):
    return device.subsystem == "usb" and device.device_type == "usb_device"

def find_usb_parent(device):
    return device.find_parent(subsystem="usb", device_type="usb_device")

def usb_device_by_node(app_context, device_node):
    try:
        return pyudev.Devices.from_device_file(app_context.udev_context, device_node)
    except pyudev.DeviceNotFoundError:
        return None

def usb_device_by_bus_port(app_context, bus, port):
    for device in app_context.udev_context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            if usb_info.busnum == bus and usb_info.root_port == port:
                return device
    return None

def usb_device_by_vid_pid(app_context, vid, pid):
    for device in app_context.udev_context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            if usb_info.vid.casefold() == vid.casefold() and usb_info.pid.casefold() == pid.casefold():
                return device
    return None

from typing import NamedTuple, Optional, List
import logging

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

    def dev_id(self):
        return f"usb{self.busnum}{self.devnum}"

    def friendly_name(self):
        return f"{self.vid}:{self.pid} ({self.vendor_name} {self.product_name})"

    @property
    def root_port(self):
        return self.ports[0] if self.ports else None # pylint: disable=unsubscriptable-object

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

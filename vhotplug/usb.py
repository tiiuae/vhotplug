from typing import NamedTuple, Optional
import logging

logger = logging.getLogger("vhotplug")

class USBInfo(NamedTuple):
    vid: Optional[str] = None
    pid: Optional[str] = None
    vendor_name: Optional[str] = None
    product_name: Optional[str] = None
    interfaces: Optional[str] = None
    device_class: Optional[int] = None
    device_subclass: Optional[int] = None
    device_protocol: Optional[int] = None

def get_usb_info(device) -> USBInfo:
    vid = device.properties.get("ID_VENDOR_ID")
    pid = device.properties.get("ID_MODEL_ID")
    vendor_name = device.properties.get("ID_VENDOR_FROM_DATABASE") or device.properties.get("ID_VENDOR")
    product_name = device.properties.get("ID_MODEL_FROM_DATABASE") or device.properties.get("ID_MODEL")
    interfaces = device.properties.get("ID_USB_INTERFACES")
    device_class = int(device.attributes.get("bDeviceClass").decode().strip(), 16)
    device_subclass = int(device.attributes.get("bDeviceSubClass").decode().strip(), 16)
    device_protocol = int(device.attributes.get("bDeviceProtocol").decode().strip(), 16)
    return USBInfo(vid, pid, vendor_name, product_name, interfaces, device_class, device_subclass, device_protocol)

def parse_usb_interfaces(interfaces):
    result = []
    if interfaces:
        try:
            interfaces = interfaces.strip(':')
            for interface in interfaces.split(':'):
                l = len(interface)
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

def is_usb_hub(interfaces):
    usb_interfaces = parse_usb_interfaces(interfaces)
    for interface in usb_interfaces:
        interface_class = interface["class"]
        if interface_class == 9:
            return True
    return False

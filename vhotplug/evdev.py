import fcntl
import logging
import struct
from typing import Any, NamedTuple

import pyudev

# Constants from Linux kernel source code
EVIOCGRAB = 0x40044590
EVIOCGNAME = 0x82004506

logger = logging.getLogger("vhotplug")


class EvdevInfo(NamedTuple):
    name: str
    sys_name: str
    bus: str
    device_node: str
    path_tag: str
    properties: property

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sys_name": self.sys_name,
            "bus": self.bus,
            "device_node": self.device_node,
            "path_tag": self.path_tag,
        }

    def friendly_name(self) -> str:
        return f"{self.name} ({self.device_node})"


def get_evdev_info(device: pyudev.Device) -> EvdevInfo:
    name = _get_evdev_name(device) or ""
    sys_name = device.sys_name
    bus = device.properties.get("ID_BUS")
    device_node = device.device_node
    path_tag = device.properties.get("ID_PATH_TAG")

    return EvdevInfo(name, sys_name, bus, device_node, path_tag, device.properties)


def is_input_device(device: pyudev.Device) -> bool:
    """Checks udev properties to determine whether it is an input device eligible for passthrough."""
    return bool(
        device.subsystem == "input" and device.sys_name.startswith("event") and device.properties.get("ID_INPUT") == "1"
    )


def _get_evdev_name(device: pyudev.Device) -> str | None:
    """Reads evdev friendly name by using EVIOCGNAME ioctl."""
    if device.device_node:
        with open(device.device_node, "rb") as dev:
            name = bytearray(256)
            fcntl.ioctl(dev, EVIOCGNAME, name)
            return name.split(b"\x00", 1)[0].decode("utf-8")
    else:
        return None


async def evdev_test_grab(device_node: str) -> bool:
    """Tries to grab a device to see if it's already attached to a VM."""
    with open(device_node, "wb") as dev:  # noqa: ASYNC230
        try:
            fcntl.ioctl(dev, EVIOCGRAB, struct.pack("i", 1))
        except OSError as e:
            logger.debug(e)
            return True
    return False

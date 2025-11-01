import logging
import fcntl
import struct
from vhotplug.qemulink import QEMULink
from vhotplug.device import log_device

# Constants from Linux kernel source code
EVIOCGRAB = 0x40044590
EVIOCGNAME = 0x82004506

logger = logging.getLogger("vhotplug")

def is_input_device(device):
    """Checks udev properties to determine whether it is an input device eligible for passthrough."""

    if device.subsystem == "input" and device.sys_name.startswith("event") and device.properties.get("ID_INPUT") == "1":
        return device.properties.get("ID_INPUT_MOUSE") == "1" or \
            (device.properties.get("ID_INPUT_KEYBOARD") == "1") or \
            (device.properties.get("ID_INPUT_TOUCHPAD") == "1") or \
            (device.properties.get("ID_INPUT_TOUCHSCREEN") == "1") or \
            (device.properties.get("ID_INPUT_TABLET") == "1")
    return False

def get_evdev_name(device):
    """Reads evdev friendly name by using EVIOCGNAME ioctl."""

    if device.device_node:
        with open(device.device_node, 'rb') as dev:
            name = bytearray(256)
            fcntl.ioctl(dev, EVIOCGNAME, name)
            return name.split(b'\x00', 1)[0].decode('utf-8')
    else:
        return None

async def test_grab(device):
    """Tries to grab a device to see if it's already attached to a VM."""

    with open(device.device_node, 'wb') as dev:
        try:
            fcntl.ioctl(dev, EVIOCGRAB, struct.pack('i', 1))
        except OSError as e:
            logger.debug(e)
            return True
    return False

async def attach_evdev_device(vm, device):
    """Attaches evdev device to QEMU."""

    vm_name = vm.get("name")
    vm_type = vm.get("type")
    if vm_type != "qemu":
        logger.error("Evdev passthrough is not supported for %s with type %s", vm_name, vm_type)
        return
    vm_socket = vm.get("socket")
    logger.info("Attaching evdev device to %s (%s)", vm_name, vm_socket)
    qemu = QEMULink(vm_socket)
    await qemu.add_evdev_device(device)

async def attach_connected_evdev(app_context):
    """Finds all non-USB evdev devices and attaches them to the selected VM."""

    vm = app_context.config.vm_for_evdev_devices()
    if vm is None:
        logger.debug("Evdev passthrough is not enabled")
        return

    logger.info("Checking connected non-USB input devices")
    for device in app_context.udev_context.list_devices(subsystem='input'):
        bus = device.properties.get("ID_BUS")
        if is_input_device(device) and bus != "usb":
            name = get_evdev_name(device)
            logger.info("Found non-USB input device: %s, bus: %s, node: %s", name, bus, device.device_node)
            log_device(device)
            if await test_grab(device):
                logger.info("The device is grabbed by another process, it is likely already connected to the VM")
            else:
                try:
                    await attach_evdev_device(vm, device)
                except RuntimeError as e:
                    logger.error("Failed to attach evdev device %s: %s", name, e)

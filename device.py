import logging
from qemulink import *
import fcntl
import struct
import psutil

EVIOCGRAB = 0x40044590
EVIOCGNAME = 0x82004506

logger = logging.getLogger("vhotplug")

def log_device(device, level=logging.DEBUG):
    try:
        logger.log(level, f"Device path: {device.device_path}")
        logger.log(level, f"  sys_path: {device.sys_path}")
        logger.log(level, f"  sys_name: {device.sys_name}")
        logger.log(level, f"  sys_number: {device.sys_number}")
        logger.log(level, f"  tags:")
        for t in device.tags:
            if t:
                logger.log(level, f"    {t}")
        logger.log(level, f"  subsystem: {device.subsystem}")
        logger.log(level, f"  driver: {device.driver}")
        logger.log(level, f"  device_type: {device.device_type}")
        logger.log(level, f"  device_node: {device.device_node}")
        logger.log(level, f"  device_number: {device.device_number}")
        logger.log(level, f"  is_initialized: {device.is_initialized}")
        logger.log(level, "  Device properties:")
        for i in device.properties:
            logger.log(level, f"    {i} = {device.properties[i]}")
        logger.log(level, "  Device attributes:")
        for a in device.attributes.available_attributes:
            logger.log(level, f"    {a}: {device.attributes.get(a)}")
    except AttributeError as e:
        logger.warn(e)

def find_usb_parent(device):
    return device.find_parent(subsystem='usb', device_type='usb_device')

async def add_usb_device(qmpsock, device):
    qemu = QEMULink(qmpsock)
    usb_dev = find_usb_parent(device)
    if usb_dev != None:
        logger.info(f"Found parent USB device {usb_dev.sys_name} for {device.sys_name}.")
        await qemu.add_usb_device(usb_dev)
    else:
        logger.error(f"Failed to find parent USB device")

async def remove_device(qmpsock, device):
    if qmpsock:
        qemu = QEMULink(qmpsock)
        await qemu.remove_usb_device(device)

def is_input_device(device):
    if device.subsystem == "input" and device.sys_name.startswith("event") and device.properties.get("ID_INPUT") == "1":
        return device.properties.get("ID_INPUT_MOUSE") == "1" or \
            (device.properties.get("ID_INPUT_KEYBOARD") == "1") or \
            (device.properties.get("ID_INPUT_TOUCHPAD") == "1")
    return False

def is_sound_device(device):
    return device.subsystem == "sound" and device.device_type != "pcm" and device.sys_name.startswith("card")

def is_disk_device(device):
    return device.subsystem == "block" and device.device_type == "disk"

def get_evdev_name(device):
    if device.device_node:
        with open(device.device_node, 'rb') as dev:
            name = bytearray(256)
            fcntl.ioctl(dev, EVIOCGNAME, name) #EVIOCGNAME
            return name.split(b'\x00', 1)[0].decode('utf-8')
    else:
        return None

async def test_grab(device):
    with open(device.device_node, 'wb') as dev:
        try:
            fcntl.ioctl(dev, EVIOCGRAB, struct.pack('i', 1))
        except OSError as e:
            logger.debug(e)
            return True
    return False

def is_root_device(context, device):
    # Find device partitions
    for udevpart in context.list_devices(subsystem='block', DEVTYPE='partition'):
        parent = udevpart.find_parent('block')
        if parent and parent.device_node == device.device_node:
            logger.info(f"Disk {device.device_node} has partition {udevpart.device_node}")
            # Find mountpoints
            partitions = psutil.disk_partitions(all=True)
            for part in partitions:
                if part.device == udevpart.device_node:
                    logger.info(f"Found mountpoint {part.mountpoint}. Filesystem: {part.fstype}. Options: {part.opts}.")
                    if part.mountpoint == "/boot":
                        return True
    return False

async def add_connected_devices(qmpinput, qmpsound, qmpdisk, context, addevdev, busprefix):
    pcieport = 1
    for device in context.list_devices(subsystem='input'):
        log_device(device)
        if is_input_device(device):
            name = get_evdev_name(device)
            bus = device.properties.get("ID_BUS")
            logger.info(f"Found input device: {name}. Bus: {bus}.")
            logger.info(f"Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
            if bus == "usb":
                await add_usb_device(qmpinput, device)
            else:
                if addevdev:
                    if await test_grab(device):
                        logger.info("The device is grabbed by another process; it is likely already connected to the VM.")
                    else:
                        #log_device(device, logging.INFO)
                        qemu = QEMULink(qmpinput)
                        await qemu.add_evdev_device(device, f"{busprefix}{pcieport}")
                        pcieport += 1
                else:
                    logger.info("Skipping non-USB input device.")
    for device in context.list_devices(subsystem='sound'):
        log_device(device)
        if qmpsound and is_sound_device(device):
            bus = device.properties.get("ID_BUS")
            logger.info(f"Found sound device: {device.sys_name}. Bus: {bus}. Path: {device.device_path}.")
            if bus == "usb":
                await add_usb_device(qmpsound, device)
            else:
                logger.warn("Bus {bus} is not supported for sound devices")
    for device in context.list_devices(subsystem='block'):
        log_device(device)
        if qmpdisk and is_disk_device(device):
            bus = device.properties.get("ID_BUS")
            if bus == "usb":
                logger.info(f"Found USB disk device: {device.sys_name}. Bus: {bus}. Node: {device.device_node}.")
                if is_root_device(context, device):
                    logger.info(f"USB drive {device.device_node} is used as a root device, skipping.")
                else:
                    await add_usb_device(qmpdisk, device)

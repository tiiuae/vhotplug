import logging
from qemulink import *

logger = logging.getLogger("vhotplug")

def log_device(device):
    try:
        logger.info(f"Device path: {device.device_path}")
        logger.info(f"  sys_path: {device.sys_path}")
        logger.info(f"  sys_name: {device.sys_name}")
        logger.info(f"  sys_number: {device.sys_number}")
        logger.info(f"  tags:")
        for t in device.tags:
            if t:
                logger.info(f"    {t}")
        logger.info(f"  subsystem: {device.subsystem}")
        logger.info(f"  driver: {device.driver}")
        logger.info(f"  device_type: {device.device_type}")
        logger.info(f"  device_node: {device.device_node}")
        logger.info(f"  device_number: {device.device_number}")
        logger.info(f"  is_initialized: {device.is_initialized}")
        logger.info("  Device properties:")
        for i in device.properties:
            logger.info(f"    {i} = {device.properties[i]}")
        logger.info("  Device attributes:")
        for a in device.attributes.available_attributes:
            logger.info(f"    {a}: {device.attributes.get(a)}")
    except AttributeError as e:
        logger.warn(e)

def find_usb_parent(device):
    return device.find_parent(subsystem='usb', device_type='usb_device')

async def add_device(qmpsock, device):
    #log_device(device)
    qemu = QEMULink(qmpsock)
    usb_dev = find_usb_parent(device)
    if usb_dev != None:
        logger.info(f"Found parent USB device {usb_dev.sys_name} for {device.sys_name}.")
        await qemu.add_usb_device(usb_dev)
    else:
        logger.error(f"Failed to find parent USB device")

async def remove_device(qmpsock, device):
    qemu = QEMULink(qmpsock)
    await qemu.remove_usb_device(device)

async def add_connected_devices(qmpsock, context):
    for device in context.list_devices(subsystem='input'):
        if device.sys_name.startswith("event") and device.properties.get("ID_INPUT") == "1":
            if (device.properties.get("ID_INPUT_MOUSE") == "1") or \
                (device.properties.get("ID_INPUT_KEYBOARD") == "1") or \
                (device.properties.get("ID_INPUT_TOUCHPAD") == "1"):
                logger.info(f"Found input device. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
                bus = device.properties.get("ID_BUS")
                if bus == None:
                    logger.warn("Unknown bus id")
                    #log_device(device)
                else:
                    if bus != "usb":
                        logger.warn(f"Bus {bus} is not supported")                    
                    else:
                        await add_device(qmpsock, device)

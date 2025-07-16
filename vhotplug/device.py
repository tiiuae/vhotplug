import logging
import fcntl
import struct
import psutil
from vhotplug.qemulink import QEMULink
from vhotplug.crosvmlink import CrosvmLink
from vhotplug.usb import get_usb_info, is_usb_hub

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

def is_usb_device(device):
    return device.subsystem == "usb" and device.device_type == "usb_device"

def find_usb_parent(device):
    return device.find_parent(subsystem="usb", device_type="usb_device")

def is_input_device(device):
    if device.subsystem == "input" and device.sys_name.startswith("event") and device.properties.get("ID_INPUT") == "1":
        return device.properties.get("ID_INPUT_MOUSE") == "1" or \
            (device.properties.get("ID_INPUT_KEYBOARD") == "1") or \
            (device.properties.get("ID_INPUT_TOUCHPAD") == "1") or \
            (device.properties.get("ID_INPUT_TOUCHSCREEN") == "1") or \
            (device.properties.get("ID_INPUT_TABLET") == "1")
    return False

def is_sound_device(device):
    return device.subsystem == "sound" and device.device_type != "pcm" and device.sys_name.startswith("card")

def is_disk_device(device):
    return device.subsystem == "block" and device.device_type == "disk"

def is_network_device(device):
    driver = device.properties.get("ID_NET_DRIVER")
    return device.subsystem == "net" and device.device_type != "bridge" and \
        driver != "tun" and driver != "bridge" and device.sys_name != "lo"

def is_smartcard(device):
    return device.subsystem == "usb" and device.properties.get("ID_SMARTCARD_READER") == "1"

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

def is_boot_device(context, device):
    # Find device partitions
    for udevpart in context.list_devices(subsystem="block", DEVTYPE="partition"):
        parent = udevpart.find_parent("usb", "usb_device")
        if parent and parent.device_node == device.device_node:
            logger.info(f"USB drive {device.device_node} has partition {udevpart.device_node}")
            # Find mountpoints
            partitions = psutil.disk_partitions(all=True)
            for part in partitions:
                if part.device == udevpart.device_node:
                    logger.info(f"Found mountpoint {part.mountpoint} with filesystem {part.fstype}")
                    logger.info(f"Options: {part.opts}")
                    if part.mountpoint == "/boot":
                        return True
    return False

async def attach_usb_device(context, config, device):
    usb_info = get_usb_info(device)
    vm = config.vm_for_usb_device(usb_info)
    if vm:
        vm_name = vm.get("name")
        vm_type = vm.get("type")
        logger.info(f"Attaching to {vm_name} ({vm_type})")
        if is_boot_device(context, device):
            logger.info(f"USB drive {device.device_node} is used as a boot device, skipping")
            return
        vm_socket = vm.get("socket")
        if vm_type == "qemu":
            qemu = QEMULink(vm_socket)
            #await qemu.add_usb_device_by_vid_pid(device, vid, pid)
            await qemu.add_usb_device(device)
        elif vm_type == "crosvm":
            crosvm = CrosvmLink(vm_socket, config.config.get("crosvm"))
            await crosvm.add_usb_device(device)
        else:
            logger.error(f"Unknown VM type: {vm_type}")
    else:
        logger.info(f"No VM found for {usb_info.vid}:{usb_info.pid}")

async def remove_usb_device(config, device):
    # Enumerate all VMs, find the one with the device attached and remove it
    for vm in config.get_all_vms():
        vm_name = vm.get("name")
        logger.info(f"Checking {vm_name}")
        vm_type = vm.get("type")
        vm_socket = vm.get("socket")
        if vm_type == "qemu":
            qemu = QEMULink(vm_socket)
            ids = await qemu.usb()
            qemuid = qemu.id_for_usb(device)
            if qemuid in ids:
                logger.info(f"Removing {qemuid} from {vm_name})")
                await qemu.remove_usb_device(device)
        elif vm_type == "crosvm":
            # Crosvm seems to automatically remove the device from the list so this code is not really used
            usb_info = get_usb_info(device)
            crosvm = CrosvmLink(vm_socket, config.config.get("crosvm"))
            devices = await crosvm.usb_list()
            for index, crosvm_vid, crosvm_pid in devices:
                if usb_info.vid == crosvm_vid and usb_info.pid == crosvm_pid:
                    logger.info(f"Removing {index} from {vm_name})")
                    await crosvm.remove_usb_device(index)
        else:
            logger.error(f"Unsupported vm type: {vm_type}")

async def attach_evdev_device(vm, busprefix, pcieport, device):
    vm_name = vm.get("name")
    vm_type = vm.get("type")
    if vm_type != "qemu":
        logger.error(f"Evdev passthrough is not supported for {vm_name} with type {vm_type}")
        return
    vm_socket = vm.get("socket")
    bus = f"{busprefix}{pcieport}"
    logger.info(f"Attaching evdev device to {vm_name} ({vm_socket}) on bus {bus}")
    qemu = QEMULink(vm_socket)
    await qemu.add_evdev_device(device, bus)

async def attach_connected_devices(context, config):
    # Non-USB evdev passthrough
    res = config.vm_for_evdev_devices()
    if res:
        vm = res[0]
        busprefix = res[1]
        pcieport = 1
        logger.info("Checking connected non-USB input devices")
        for device in context.list_devices(subsystem='input'):
            bus = device.properties.get("ID_BUS")
            if is_input_device(device) and bus != "usb":
                name = get_evdev_name(device)
                logger.info(f"Found non-USB input device: {name}")
                logger.info(f"Bus: {bus}, node: {device.device_node}")
                log_device(device)
                if await test_grab(device):
                    logger.info("The device is grabbed by another process, it is likely already connected to the VM")
                else:
                    await attach_evdev_device(vm, busprefix, pcieport, device)
                    pcieport += 1

    # Check USB devices
    logger.info("Checking connected USB devices")
    for device in context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.info(f"Found USB device {usb_info.vid}:{usb_info.pid} ({usb_info.vendor_name} {usb_info.product_name}): {device.device_node}")
            logger.info(f'Device class: "{usb_info.device_class}", subclass: "{usb_info.device_subclass}", protocol: "{usb_info.device_protocol}", interfaces: "{usb_info.interfaces}"')
            log_device(device)
            if is_usb_hub(usb_info.interfaces):
                logger.info(f"USB device {usb_info.vid}:{usb_info.pid} is a USB hub, skipping")
                continue
            await attach_usb_device(context, config, device)

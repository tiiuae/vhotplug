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
        logger.log(level, "Device path: %s", device.device_path)
        logger.log(level, "  sys_path: %s", device.sys_path)
        logger.log(level, "  sys_name: %s", device.sys_name)
        logger.log(level, "  sys_number: %s", device.sys_number)
        logger.log(level, "  tags:")
        for t in device.tags:
            if t:
                logger.log(level, "    %s", t)
        logger.log(level, "  subsystem: %s", device.subsystem)
        logger.log(level, "  driver: %s", device.driver)
        logger.log(level, "  device_type: %s", device.device_type)
        logger.log(level, "  device_node: %s", device.device_node)
        logger.log(level, "  device_number: %s", device.device_number)
        logger.log(level, "  is_initialized: %s", device.is_initialized)
        logger.log(level, "  Device properties:")
        for i in device.properties:
            logger.log(level, "    %s = %s", i, device.properties[i])
        logger.log(level, "  Device attributes:")
        for a in device.attributes.available_attributes:
            logger.log(level, "    %s: %s", a, device.attributes.get(a))
    except AttributeError as e:
        logger.warning(e)

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

def is_boot_device(context, usb_info):
    # Find device partitions
    for udevpart in context.list_devices(subsystem="block", DEVTYPE="partition"):
        parent = udevpart.find_parent("usb", "usb_device")
        if parent and parent.device_node == usb_info.device_node:
            logger.debug("USB drive %s has partition %s", usb_info.device_node, udevpart.device_node)
            # Find mountpoints
            partitions = psutil.disk_partitions(all=True)
            for part in partitions:
                if part.device == udevpart.device_node:
                    logger.debug("Found mountpoint %s with filesystem %s", part.mountpoint, part.fstype)
                    logger.debug("Options: %s", part.opts)
                    if part.mountpoint == "/boot":
                        return True
    return False

# pylint: disable = too-many-positional-arguments
async def vm_for_usb_device(context, config, api_server, usb_info, selected_vm, ask):
    if is_boot_device(context, usb_info):
        logger.info("USB drive %s is used as a boot device", usb_info.device_node)
        return None

    res = config.vm_for_usb_device(usb_info)
    if not res:
        logger.info("No VM found for %s:%s", usb_info.vid, usb_info.pid)
        return None

    target_vm = res[0]
    allowed_vms = res[1]
    if target_vm:
        if selected_vm and selected_vm != target_vm:
            raise RuntimeError(f"Selected vm {selected_vm} but target vm is set to {target_vm}")
    else:
        if allowed_vms is None:
            raise RuntimeError("No allowed VMs")
        if selected_vm:
            if selected_vm not in allowed_vms:
                raise RuntimeError(f"Selected VM {selected_vm} is not allowed")
            target_vm = selected_vm
        else:
            logger.info("Found multiple VMs for %s:%s", usb_info.vid, usb_info.pid)
            if ask:
                if api_server:
                    api_server.notify_usb_select_vm(usb_info, allowed_vms)
                return None
            target_vm = allowed_vms[0]

    vm = config.get_vm(target_vm)
    if not vm:
        raise RuntimeError(f"VM {target_vm} is not found")
    return vm

async def attach_usb_device(config, api_server, usb_info, vm):
    vm_name = vm.get("name")
    vm_type = vm.get("type")
    logger.info("Attaching to %s (%s)", vm_name, vm_type)
    vm_socket = vm.get("socket")
    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        #await qemu.add_usb_device_by_vid_pid(usb_info)
        await qemu.add_usb_device(usb_info)
        if api_server:
            api_server.notify_usb_attached(usb_info, vm_name)
    elif vm_type == "crosvm":
        crosvm = CrosvmLink(vm_socket, config.config.get("general", {}).get("crosvm"))
        await crosvm.add_usb_device(usb_info)
        if api_server:
            api_server.notify_usb_attached(usb_info, vm_name)
    else:
        raise RuntimeError(f"Unknown VM type: {vm_type}")

async def remove_usb_device(config, usb_info, api_server):
    # Enumerate all VMs, find the one with the device attached and remove it
    for vm in config.get_all_vms():
        vm_name = vm.get("name")
        logger.info("Checking %s", vm_name)
        vm_type = vm.get("type")
        vm_socket = vm.get("socket")
        if vm_type == "qemu":
            qemu = QEMULink(vm_socket)
            ids = await qemu.usb()
            qemuid = usb_info.dev_id()
            if qemuid in ids:
                logger.info("Removing %s from %s", qemuid, vm_name)
                await qemu.remove_usb_device(usb_info)
                if api_server:
                    api_server.notify_usb_detached(usb_info, vm_name)
        elif vm_type == "crosvm":
            # Crosvm seems to automatically remove the device from the list so this code is not really used
            crosvm = CrosvmLink(vm_socket, config.config.get("crosvm"))
            devices = await crosvm.usb_list()
            for index, crosvm_vid, crosvm_pid in devices:
                if usb_info.vid == crosvm_vid and usb_info.pid == crosvm_pid:
                    logger.info("Removing %s from %s", index, vm_name)
                    await crosvm.remove_usb_device(index)
                    if api_server:
                        api_server.notify_usb_detached(usb_info, vm_name)
        else:
            logger.error("Unsupported vm type: %s", vm_type)
            raise RuntimeError(f"Unsupported vm type: {vm_type}")

async def attach_evdev_device(vm, busprefix, pcieport, device):
    """Attaches evdev device to QEMU."""
    vm_name = vm.get("name")
    vm_type = vm.get("type")
    if vm_type != "qemu":
        logger.error("Evdev passthrough is not supported for %s with type %s", vm_name, vm_type)
        return
    vm_socket = vm.get("socket")
    bus = f"{busprefix}{pcieport}"
    logger.info("Attaching evdev device to %s (%s) on bus %s", vm_name, vm_socket, bus)
    qemu = QEMULink(vm_socket)
    await qemu.add_evdev_device(device, bus)

async def attach_connected_devices(context, config):
    """Finds all evdev and USB devices that match the rules from the config and attaches them to VMs."""
    # Non-USB evdev passthrough
    res = config.vm_for_evdev_devices()
    if res:
        vm = res[0]
        busprefix = res[1]
        if not vm:
            logger.info("Evdev passthrough is not enabled")
        else:
            pcieport = 1
            logger.info("Checking connected non-USB input devices")
            for device in context.list_devices(subsystem='input'):
                bus = device.properties.get("ID_BUS")
                if is_input_device(device) and bus != "usb":
                    name = get_evdev_name(device)
                    logger.info("Found non-USB input device: %s, bus: %s, node: %s", name, bus, device.device_node)
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
            logger.debug("Found USB device %s:%s (%s %s): %s", usb_info.vid, usb_info.pid, usb_info.vendor_name, usb_info.product_name, device.device_node)
            logger.debug('Device class: "%s", subclass: "%s", protocol: "%s", interfaces: "%s"', usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol, usb_info.interfaces)
            log_device(device)
            if is_usb_hub(usb_info.interfaces):
                logger.debug("USB device %s:%s is a USB hub, skipping", usb_info.vid, usb_info.pid)
                continue
            try:
                vm = await vm_for_usb_device(context, config, None, usb_info, None, False)
                if vm:
                    await attach_usb_device(config, None, usb_info, vm)
            except RuntimeError as e:
                logger.error("Failed to attach device: %s", e)

def get_usb_devices(context, config):
    """Returns a list of all USB devices that match the rules from the config."""
    usb_list = []
    for device in context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            if is_usb_hub(usb_info.interfaces):
                continue
            if is_boot_device(context, usb_info):
                continue
            res = config.vm_for_usb_device(usb_info)
            if res:
                usb_device = usb_info.to_dict()
                if res[0]:
                    usb_device["vm"] = res[0]
                if res[1]:
                    usb_device["allowed_vms"] = res[1]
                usb_list.append(usb_device)
    return usb_list

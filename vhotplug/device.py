import logging
import fcntl
import struct
import psutil
import pyudev
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
        # Logging all attributes might completely freeze the app when a YubiKey is connected to the host
        #for a in device.attributes.available_attributes:
            #logger.log(level, "    %s: %s", a, device.attributes.get(a))
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

def _autoselect_vm(app_context, usb_info, allowed_vms):
    ''' Select the last used VM from the state database or fall back to the first one in the list of allowed vms'''

    current_vm_name = app_context.usb_state.get_selected_vm_for_device(usb_info)
    if current_vm_name and current_vm_name in allowed_vms:
        logger.info("Selecting %s from the state database", current_vm_name)
        return current_vm_name

    logger.info("Selecting %s as first option", allowed_vms[0])
    return allowed_vms[0]

async def attach_usb_device(app_context, usb_info, ask):
    '''Find a VM and attach a USB device when it is plugged in or detected at startup.'''

    # Find a rule for the device in the config file
    (target_vm, allowed_vms) = app_context.config.vm_for_usb_device(usb_info)
    if not target_vm and not allowed_vms:
        logger.debug("No VM found for %s:%s", usb_info.vid, usb_info.pid)
        return

    if is_usb_hub(usb_info.interfaces):
        logger.debug("USB device %s is a USB hub, skipping", usb_info.friendly_name())
        return

    if is_boot_device(app_context.udev_context, usb_info):
        logger.debug("USB drive %s is used as a boot device", usb_info.friendly_name())
        return

    # Check if the user manually disconnected the device before
    if app_context.usb_state.is_disconnected(usb_info):
        logger.info("Device %s was forcibly disconnected", usb_info.friendly_name())
        if app_context.api_server:
            app_context.api_server.notify_usb_attached(usb_info, None)
        return

    if not target_vm:
        logger.info("Found multiple VMs for %s", usb_info.friendly_name())
        if ask:
            # Send a notification without a VM name and then ask for VM selection
            if app_context.api_server:
                app_context.api_server.notify_usb_attached(usb_info, None)
            logger.info("Sending an API request to select a VM")
            if app_context.api_server:
                app_context.api_server.notify_usb_select_vm(usb_info, allowed_vms)
            return

        # Try to select the last used VM from the state database or fall back to the first one in the list
        target_vm = _autoselect_vm(app_context, usb_info, allowed_vms)

    await attach_usb_device_to_vm(app_context, usb_info, target_vm)

async def attach_usb_device_to_vm(app_context, usb_info, vm_name):
    '''Gets VM details and attaches a USB device.'''

    # Get VM details from the config
    vm = app_context.config.get_vm(vm_name)
    if not vm:
        raise RuntimeError(f"VM {vm_name} is not found in the config file")

    logger.info("Attaching %s to %s", usb_info.friendly_name(), vm_name)

    # Check if the device is attached to a different VM and remove
    current_vm_name = app_context.usb_state.get_vm_for_device(usb_info)
    if current_vm_name and current_vm_name != vm_name:
        logger.warning("Device is attached to %s, removing...", current_vm_name)
        try:
            remove_usb_device(app_context, usb_info)
        except RuntimeError as e:
            logger.warning("Failed to remove: %s", e)

    # Attach device to the VM
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        #await qemu.add_usb_device_by_vid_pid(usb_info)
        await qemu.add_usb_device(usb_info)
    elif vm_type == "crosvm":
        crosvm = CrosvmLink(vm_socket, app_context.config.config.get("general", {}).get("crosvm"))
        await crosvm.add_usb_device(usb_info)
    else:
        raise RuntimeError(f"Unknown VM type: {vm_type}")

    # Add selected VM to the state database
    app_context.usb_state.set_vm_for_device(usb_info, vm_name)
    app_context.usb_state.clear_disconnected(usb_info)

    if app_context.api_server:
        app_context.api_server.notify_usb_attached(usb_info, vm_name)

async def remove_usb_device(app_context, usb_info):
    '''Find a VM selected for the USB device and remove it.'''

    # Get current VM for the device from the state database
    current_vm_name = app_context.usb_state.get_vm_for_device(usb_info)
    if not current_vm_name:
        raise RuntimeError("VM not found for device")

    logger.info("Removing %s from %s", usb_info.device_node, current_vm_name)

    # Check if the VM is valid
    vm = app_context.config.get_vm(current_vm_name)
    if not vm:
        raise RuntimeError(f"VM {current_vm_name} not found in the configuration file")

    # Remove device from VM
    vm_name = vm.get("name")
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        qemuid = usb_info.dev_id()
        logger.debug("Removing %s from %s", qemuid, vm_name)
        await qemu.remove_usb_device(usb_info)
    elif vm_type == "crosvm":
        # Crosvm seems to automatically remove the device from the list so this code is not really used
        crosvm = CrosvmLink(vm_socket, app_context.config.config.get("crosvm"))
        devices = await crosvm.usb_list()
        for index, crosvm_vid, crosvm_pid in devices:
            if usb_info.vid == crosvm_vid and usb_info.pid == crosvm_pid:
                logger.debug("Removing %s from %s", index, vm_name)
                await crosvm.remove_usb_device(index)
    else:
        raise RuntimeError(f"Unsupported vm type: {vm_type}")

    # Remove it from the state database
    app_context.usb_state.remove_vm_for_device(usb_info)

    if app_context.api_server:
        app_context.api_server.notify_usb_detached(usb_info, vm_name)

async def _attach_existing_usb_device(app_context, device, selected_vm):
    '''Attach an existing USB device at the user's request.'''

    usb_info = get_usb_info(device)

    # Don't allow attaching a USB drive used as a boot device
    if is_boot_device(app_context.udev_context, usb_info):
        raise RuntimeError(f"USB drive {usb_info.friendly_name()} is used as a boot device")

    # Find a rule for the device in the config file
    (target_vm, allowed_vms) = app_context.config.vm_for_usb_device(usb_info)
    if target_vm:
        if selected_vm and selected_vm != target_vm:
            raise RuntimeError(f"Selected VM {selected_vm} but target VM is set to {target_vm}")
    else:
        if allowed_vms is None:
            raise RuntimeError("No allowed VMs defined")
        if selected_vm:
            if selected_vm not in allowed_vms:
                raise RuntimeError(f"Selected VM {selected_vm} is not allowed")

            # Save VM selection when multiple VMs are allowed
            target_vm = selected_vm
            app_context.usb_state.select_vm_for_device(usb_info, target_vm)
        else:
            # Try to select the last used VM from the state database or fall back to the first one in the list
            target_vm = _autoselect_vm(app_context, usb_info, allowed_vms)

    # Attach device to the VM
    await attach_usb_device_to_vm(app_context, usb_info, target_vm)

def _usb_device_by_node(app_context, device_node):
    try:
        return pyudev.Devices.from_device_file(app_context.udev_context, device_node)
    except pyudev.DeviceNotFoundError:
        raise RuntimeError(f"USB device {device_node} not found in the system") from None

def _usb_device_by_bus_port(app_context, bus, port):
    for device in app_context.udev_context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            if usb_info.busnum == bus and usb_info.root_port == port:
                return device
    raise RuntimeError(f"USB device with bus {bus} and port {port} not found in the system")

def _usb_device_by_vid_pid(app_context, vid, pid):
    for device in app_context.udev_context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            if usb_info.vid.casefold() == vid.casefold() and usb_info.pid.casefold() == pid.casefold():
                return device
    raise RuntimeError(f"USB device {vid}:{pid} not found in the system")

async def attach_existing_usb_device(app_context, device_node, selected_vm):
    device = _usb_device_by_node(app_context, device_node)
    await _attach_existing_usb_device(app_context, device, selected_vm)

async def attach_existing_usb_device_by_bus_port(app_context, bus, port, selected_vm):
    device = _usb_device_by_bus_port(app_context, bus, port)
    await _attach_existing_usb_device(app_context, device, selected_vm)

async def attach_existing_usb_device_by_vid_pid(app_context, vid, pid, selected_vm):
    device = _usb_device_by_vid_pid(app_context, vid, pid)
    await _attach_existing_usb_device(app_context, device, selected_vm)

async def _remove_existing_usb_device(app_context, device, permanent = False):
    '''Remove an existing USB device at the user's request.'''

    usb_info = get_usb_info(device)

    # Remove device from the VM
    await remove_usb_device(app_context, usb_info)

    # Mark the device as forcibly disconnected
    if permanent:
        app_context.usb_state.set_disconnected(usb_info)

async def remove_existing_usb_device(app_context, device_node, permanent = False):
    device = _usb_device_by_node(app_context, device_node)
    await _remove_existing_usb_device(app_context, device, permanent)

async def remove_existing_usb_device_by_bus_port(app_context, bus, port, permanent = False):
    device = _usb_device_by_bus_port(app_context, bus, port)
    await _remove_existing_usb_device(app_context, device, permanent)

async def remove_existing_usb_device_by_vid_pid(app_context, vid, pid, permanent = False):
    device = _usb_device_by_vid_pid(app_context, vid, pid)
    await _remove_existing_usb_device(app_context, device, permanent)

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

async def attach_connected_evdev(app_context):
    """Non-USB evdev passthrough."""

    vm, busprefix = app_context.config.vm_for_evdev_devices()
    if vm is None:
        logger.debug("Evdev passthrough is not enabled")
        return

    pcieport = 1
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
                await attach_evdev_device(vm, busprefix, pcieport, device)
                pcieport += 1

async def attach_connected_devices(app_context):
    """Finds all evdev and USB devices that match the rules from the config and attaches them to VMs."""

    logger.info("Checking connected USB devices")
    for device in app_context.udev_context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.debug("Found USB device %s:%s (%s %s): %s", usb_info.vid, usb_info.pid, usb_info.vendor_name, usb_info.product_name, device.device_node)
            logger.debug('Device class: "%s", subclass: "%s", protocol: "%s", interfaces: "%s"', usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol, usb_info.interfaces)
            log_device(device)

            try:
                await attach_usb_device(app_context, usb_info, False)
            except RuntimeError as e:
                logger.error("Failed to attach device %s: %s", usb_info.friendly_name(), e)

async def detach_connected_devices(app_context):
    """Detach all connected devices from VMs."""

    logger.info("Detaching all USB devices")
    for device_node in app_context.usb_state.list_devices():
        await remove_existing_usb_device(app_context, device_node)

def get_usb_devices(app_context):
    """Returns a list of all USB devices that match the rules from the config."""

    usb_list = []
    for device in app_context.udev_context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)

            if is_usb_hub(usb_info.interfaces):
                continue

            if is_boot_device(app_context.udev_context, usb_info):
                continue

            (target_vm, allowed_vms) = app_context.config.vm_for_usb_device(usb_info)
            if target_vm or allowed_vms:

                # Convert USB device info to a dictionary
                usb_device = usb_info.to_dict()

                # Add allowed VMs
                if target_vm:
                    usb_device["allowed_vms"] = [target_vm]
                else:
                    usb_device["allowed_vms"] = allowed_vms

                # Get current VM name
                current_vm_name = app_context.usb_state.get_vm_for_device(usb_info)
                if current_vm_name and not app_context.usb_state.is_disconnected(usb_info):
                    usb_device["vm"] = current_vm_name
                else:
                    usb_device["vm"] = None

                usb_list.append(usb_device)
    return usb_list

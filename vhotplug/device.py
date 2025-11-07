import logging
from vhotplug.qemulink import QEMULink
from vhotplug.crosvmlink import CrosvmLink
from vhotplug.usb import USBInfo, get_usb_info, is_usb_hub, is_usb_device, usb_device_by_node, usb_device_by_bus_port, usb_device_by_vid_pid
from vhotplug.pci import PCIInfo, get_pci_info, pci_info_by_address, pci_info_by_vid_did, setup_vfio, get_iommu_group_devices

logger = logging.getLogger("vhotplug")

def log_device(device, level=logging.DEBUG):
    """Logs all udev device properties for debugging."""

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

def _autoselect_vm(app_context, dev_info, allowed_vms):
    """Select the last used VM from the state database or fall back to the first one in the list of allowed vms"""

    current_vm_name = app_context.dev_state.get_selected_vm_for_device(dev_info)
    if current_vm_name and current_vm_name in allowed_vms:
        logger.info("Selecting %s from the state database", current_vm_name)
        return current_vm_name

    logger.info("Selecting %s as first option", allowed_vms[0])
    return allowed_vms[0]

# pylint: disable=too-many-branches,too-many-return-statements
async def attach_device(app_context, dev_info, ask, vms_scope = None):
    """Find a VM and attach a device when it is plugged in or detected at startup."""

    # Find a rule for the device in the config file
    res = app_context.config.vm_for_device(dev_info)
    if not res:
        logger.debug("No VM found for %s", dev_info.friendly_name())
        return

    # Don't allow to attach device that is used to boot the system
    if dev_info.is_boot_device(app_context.udev_context):
        logger.debug("Device %s is used as a boot device", dev_info.friendly_name())
        return

    # Check if the user manually disconnected the device before
    if app_context.dev_state.is_disconnected(dev_info):
        logger.info("Device %s was forcibly disconnected", dev_info.friendly_name())
        return

    target_vm = res.target_vm
    if not target_vm:
        if isinstance(dev_info, USBInfo):
            logger.info("Found multiple VMs for %s", dev_info.friendly_name())
            if ask:
                logger.info("Sending an API request to select a VM")
                if app_context.api_server:
                    app_context.api_server.notify_usb_select_vm(dev_info, res.allowed_vms)
                return
        else:
            logger.error("Multiple VMs for %s are not allowed (only supported for USB)", dev_info.friendly_name())

        # Try to select the last used VM from the state database or fall back to the first one in the list
        target_vm = _autoselect_vm(app_context, dev_info, res.allowed_vms)

    if vms_scope and target_vm not in vms_scope:
        logger.debug("Skipping %s because its VM is %s while only devices for %s are processed", dev_info.friendly_name(), target_vm, vms_scope)
        return

    # For PCI devices, check IOMMU group
    if isinstance(dev_info, PCIInfo):
        devices = get_iommu_group_devices(dev_info.address)
        if len(devices) > 1:
            logger.info("Device %s has %s devices in the same IOMMU group", dev_info.friendly_name(), len(devices))
            if res.pci_iommu_add_all:
                logger.info("Adding all devices from IOMMU group")
                for address in devices:
                    pci_info = pci_info_by_address(app_context, address)
                    if pci_info:
                        await _attach_device_to_vm(app_context, pci_info, target_vm)
                return
            if res.pci_iommu_skip_if_shared:
                logger.info("Skipping device since it shares IOMMU group with other devices")
                return

    await _attach_device_to_vm(app_context, dev_info, target_vm)

async def _attach_existing_device(app_context, dev_info, selected_vm):
    """Attach an existing device at the user's request."""

    # Don't allow attaching a USB drive used as a boot device
    if dev_info.is_boot_device(app_context.udev_context):
        raise RuntimeError(f"Device {dev_info.friendly_name()} is used as a boot device")

    # Find a rule for the device in the config file
    res = app_context.config.vm_for_device(dev_info)
    if not res:
        raise RuntimeError(f"No VM found for {dev_info.friendly_name()}")

    target_vm = res.target_vm
    if target_vm:
        if selected_vm and selected_vm != target_vm:
            raise RuntimeError(f"Selected VM {selected_vm} but target VM is set to {target_vm}")
    else:
        if res.allowed_vms is None:
            raise RuntimeError("No allowed VMs defined")
        if selected_vm:
            if selected_vm not in res.allowed_vms:
                raise RuntimeError(f"Selected VM {selected_vm} is not allowed")

            # Save VM selection when multiple VMs are allowed
            target_vm = selected_vm
            app_context.dev_state.select_vm_for_device(dev_info, target_vm)
        else:
            # Try to select the last used VM from the state database or fall back to the first one in the list
            target_vm = _autoselect_vm(app_context, dev_info, res.allowed_vms)

    # Attach device to the VM
    await _attach_device_to_vm(app_context, dev_info, target_vm)

async def _attach_device_to_vm(app_context, dev_info, vm_name):
    """Gets VM details and attaches a device."""

    # Get VM details from the config
    vm = app_context.config.get_vm(vm_name)
    if not vm:
        raise RuntimeError(f"VM {vm_name} is not found in the config file")

    logger.info("Attaching %s to %s", dev_info.friendly_name(), vm_name)

    # Check if the device is attached to a different VM and remove
    current_vm_name = app_context.dev_state.get_vm_for_device(dev_info)
    if current_vm_name and current_vm_name != vm_name:
        logger.warning("Device is attached to %s, removing...", current_vm_name)
        try:
            await remove_device(app_context, dev_info)
        except RuntimeError as e:
            logger.warning("Failed to remove: %s", e)

    is_usb_dev = isinstance(dev_info, USBInfo)

    # Setup VFIO for all PCI devices in the IOMMU group if needed
    if not is_usb_dev:
        setup_vfio(dev_info)

    # Attach device to the VM
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        if is_usb_dev:
            #await qemu.add_usb_device_by_vid_pid(usb_info)
            await qemu.add_usb_device(dev_info)
        else:
            await qemu.add_pci_device(dev_info)
    elif vm_type == "crosvm":
        crosvm = CrosvmLink(vm_socket, app_context.config.config.get("general", {}).get("crosvm"))
        if is_usb_dev:
            await crosvm.add_usb_device(dev_info)
        else:
            await crosvm.add_pci_device(dev_info)
    else:
        raise RuntimeError(f"Unknown VM type: {vm_type}")

    # Add selected VM to the state database
    app_context.dev_state.set_vm_for_device(dev_info, vm_name)
    app_context.dev_state.clear_disconnected(dev_info)

    if app_context.api_server:
        app_context.api_server.notify_dev_attached(dev_info, vm_name, is_usb_dev)

async def remove_device(app_context, dev_info):
    """Find a VM selected for the device and remove it."""

    # Get current VM for the device from the state database
    current_vm_name = app_context.dev_state.get_vm_for_device(dev_info)
    if not current_vm_name:
        raise RuntimeError(f"VM not found for {dev_info.friendly_name()}")

    # Find a rule for the device in the config file
    res = app_context.config.vm_for_device(dev_info)
    if not res:
        raise RuntimeError(f"Device {dev_info.friendly_name()} doesn't match any rules")

    logger.info("Removing %s from %s", dev_info.friendly_name(), current_vm_name)

    # Check if the VM is valid
    vm = app_context.config.get_vm(current_vm_name)
    if not vm:
        raise RuntimeError(f"VM {current_vm_name} not found in the configuration file")

    # For PCI devices, check IOMMU group
    if isinstance(dev_info, PCIInfo):
        devices = get_iommu_group_devices(dev_info.address)
        if len(devices) > 1:
            logger.info("Device %s has %s devices in the same IOMMU group", dev_info.friendly_name(), len(devices))
            if res.pci_iommu_add_all:
                logger.info("Removing all devices from IOMMU group")
                for address in devices:
                    pci_info = pci_info_by_address(app_context, address)
                    if pci_info:
                        await _remove_device_from_vm(app_context, pci_info, vm)
                return
            if res.pci_iommu_skip_if_shared:
                logger.info("Skipping device since it shares IOMMU group with other devices")
                return

    await _remove_device_from_vm(app_context, dev_info, vm)

async def _remove_device_from_vm(app_context, dev_info, vm):
    """Removes device from VM."""

    vm_name = vm.get("name")
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    is_usb_dev = isinstance(dev_info, USBInfo)
    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        if is_usb_dev:
            await qemu.remove_usb_device(dev_info)
        else:
            # Here we can detach the device by its QEMU ID if vhotplug manages all devices.
            # This would be the most efficient and simple method but it won't work if the device was attached by something else since we don't know the QEMU ID.
            #await qemu.remove_pci_device(dev_info)
            # This method searches for a device by enumerating guest PCI devices and comparing the vendor ID and device ID.
            # It is less efficient but allows support for devices that were added via QEMU command line or by another manager.
            await qemu.remove_pci_device_by_vid_did(dev_info)

    elif vm_type == "crosvm":
        # Crosvm seems to automatically remove the device from the list so this code is not really used
        crosvm = CrosvmLink(vm_socket, app_context.config.config.get("crosvm"))
        if is_usb_dev:
            crosvm.remove_usb_device(dev_info)
        else:
            crosvm.remove_pci_device(dev_info)
    else:
        raise RuntimeError(f"Unsupported vm type: {vm_type}")

    # Remove it from the state database
    app_context.dev_state.remove_vm_for_device(dev_info)

    if app_context.api_server:
        app_context.api_server.notify_dev_detached(dev_info, vm_name, is_usb_dev)

async def _remove_existing_device(app_context, dev_info, permanent = False):
    """Remove existing device at the user's request."""

    # Remove device from the VM
    await remove_device(app_context, dev_info)

    # Mark the device as forcibly disconnected
    if permanent:
        app_context.dev_state.set_disconnected(dev_info)

async def attach_existing_usb_device(app_context, device_node, selected_vm):
    device = usb_device_by_node(app_context, device_node)
    if not device:
        raise RuntimeError(f"USB device {device_node} not found in the system")
    usb_info = get_usb_info(device)
    await _attach_existing_device(app_context, usb_info, selected_vm)

async def attach_existing_usb_device_by_bus_port(app_context, bus, port, selected_vm):
    device = usb_device_by_bus_port(app_context, bus, port)
    if not device:
        raise RuntimeError(f"USB device with bus {bus} and port {port} not found in the system")
    usb_info = get_usb_info(device)
    await _attach_existing_device(app_context, usb_info, selected_vm)

async def attach_existing_usb_device_by_vid_pid(app_context, vid, pid, selected_vm):
    device = usb_device_by_vid_pid(app_context, vid, pid)
    if not device:
        raise RuntimeError(f"USB device {vid}:{pid} not found in the system")
    usb_info = get_usb_info(device)
    await _attach_existing_device(app_context, usb_info, selected_vm)

async def remove_existing_usb_device(app_context, device_node, permanent = False):
    device = usb_device_by_node(app_context, device_node)
    if not device:
        raise RuntimeError(f"USB device {device_node} not found in the system")
    usb_info = get_usb_info(device)
    await _remove_existing_device(app_context, usb_info, permanent)

async def remove_existing_usb_device_by_bus_port(app_context, bus, port, permanent = False):
    device = usb_device_by_bus_port(app_context, bus, port)
    if not device:
        raise RuntimeError(f"USB device with bus {bus} and port {port} not found in the system")
    usb_info = get_usb_info(device)
    await _remove_existing_device(app_context, usb_info, permanent)

async def remove_existing_usb_device_by_vid_pid(app_context, vid, pid, permanent = False):
    device = usb_device_by_vid_pid(app_context, vid, pid)
    if not device:
        raise RuntimeError(f"USB device {vid}:{pid} not found in the system")
    usb_info = get_usb_info(device)
    await _remove_existing_device(app_context, usb_info, permanent)

async def attach_connected_usb(app_context, vms_scope = None):
    """Finds all USB devices that match the rules from the config and attaches them to VMs."""

    if vms_scope is None:
        logger.info("Attaching all USB devices")
    else:
        logger.info("Attaching USB devices for %s", vms_scope)

    for device in app_context.udev_context.list_devices(subsystem='usb'):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.debug("Found USB device %s: %s", usb_info.friendly_name(), usb_info.device_node)
            logger.debug('Device class: "%s", subclass: "%s", protocol: "%s", interfaces: "%s"', usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol, usb_info.interfaces)
            log_device(device)

            if is_usb_hub(usb_info.interfaces):
                logger.debug("USB device %s is a USB hub, skipping", usb_info.friendly_name())
                continue

            try:
                await attach_device(app_context, usb_info, False, vms_scope)
            except RuntimeError as e:
                logger.error("Failed to attach USB device %s: %s", usb_info.friendly_name(), e)

async def detach_connected_usb(app_context, vms_scope = None):
    """Detach all connected USB devices from VMs."""

    if vms_scope is None:
        logger.info("Detaching all USB devices")
    else:
        logger.info("Detaching USB devices from %s", vms_scope)

    for device_node in app_context.dev_state.list_usb_devices():
        device = usb_device_by_node(app_context, device_node)
        if device is None:
            logger.warning("Device %s not found in the system", device_node)
        else:
            # Find a rule for the device in the config file
            usb_info = get_usb_info(device)
            res = app_context.config.vm_for_device(usb_info)
            if res:
                if res.skip_on_suspend:
                    logger.info("Skipping USB device %s during suspend", usb_info.friendly_name())
                    continue

                if vms_scope:
                    current_vm_name = app_context.dev_state.get_vm_for_device(usb_info)
                    if current_vm_name not in vms_scope:
                        logger.debug("Skipping USB device %s attached to %s while only devices for %s are processed", usb_info.friendly_name(), current_vm_name, vms_scope)
                        continue
                try:
                    await _remove_existing_device(app_context, usb_info)
                except RuntimeError as e:
                    logger.error("Failed to remove %s: %s", usb_info.friendly_name(), e)
            else:
                logger.warning("Device %s does not match any rules", usb_info.friendly_name())

async def attach_existing_pci_device(app_context, pci_address, selected_vm):
    """Find PCI device by address and attach to selected VM."""

    pci_info = pci_info_by_address(app_context, pci_address)
    if not pci_info:
        raise RuntimeError(f"PCI device {pci_address} not found in the system")
    await _attach_existing_device(app_context, pci_info, selected_vm)

async def attach_existing_pci_device_by_vid_did(app_context, vid, did, selected_vm):
    """Find PCI device by vendor ID and device ID and attach to selected VM."""

    pci_info = pci_info_by_vid_did(app_context, int(vid, 16), int(did, 16))
    if not pci_info:
        raise RuntimeError(f"PCI device {vid}:{did} not found in the system")
    await _attach_existing_device(app_context, pci_info, selected_vm)
    return True

async def remove_existing_pci_device(app_context, pci_address, permanent = False):
    """Find PCI device by address and detach from VM."""

    pci_info = pci_info_by_address(app_context, pci_address)
    if not pci_info:
        raise RuntimeError(f"PCI device {pci_address} not found in the system")
    await _remove_existing_device(app_context, pci_info, permanent)

async def remove_existing_pci_device_by_vid_did(app_context, vid, did, permanent = False):
    """Find PCI device by vendor ID and device ID and detach from VM."""

    pci_info = pci_info_by_vid_did(app_context, int(vid, 16), int(did, 16))
    if not pci_info:
        raise RuntimeError(f"PCI device {vid}:{did} not found in the system")
    await _remove_existing_device(app_context, pci_info, permanent)
    return True

async def attach_connected_pci(app_context, vms_scope = None):
    """Finds all PCI devices that match the rules from the config and attaches them to VMs."""

    if vms_scope is None:
        logger.info("Attaching all PCI devices")
    else:
        logger.info("Attaching PCI devices for %s", vms_scope)

    for device in app_context.udev_context.list_devices(subsystem='pci'):
        pci_info = get_pci_info(device)
        logger.debug("Found PCI device %s: %s", pci_info.friendly_name(), pci_info.address)
        logger.debug('PCI class: "%s", subclass: "%s", prog if: "%s", driver: "%s"', pci_info.pci_class, pci_info.pci_subclass, pci_info.pci_prog_if, pci_info.driver)
        log_device(device)

        try:
            await attach_device(app_context, pci_info, False, vms_scope)
        except RuntimeError as e:
            logger.error("Failed to attach PCI device %s: %s", pci_info.friendly_name(), e)

async def detach_connected_pci(app_context, vms_scope = None):
    """Detach all connected PCI devices from VMs."""

    if vms_scope is None:
        logger.info("Detaching all PCI devices")
    else:
        logger.info("Detaching PCI devices from %s", vms_scope)

    for pci_address in app_context.dev_state.list_pci_devices():
        pci_info = pci_info_by_address(app_context, pci_address)
        if pci_info is None:
            logger.warning("Device %s not found in the system", pci_address)
        else:
            # Find a rule for the device in the config file
            res = app_context.config.vm_for_device(pci_info)
            if res:
                if vms_scope:
                    current_vm_name = app_context.dev_state.get_vm_for_device(pci_info)
                    if current_vm_name not in vms_scope:
                        logger.debug("Skipping PCI device %s attached to %s while only devices for %s are processed", pci_info.friendly_name(), current_vm_name, vms_scope)
                        continue

                if res.skip_on_suspend:
                    logger.info("Skipping PCI device %s during suspend", pci_info.friendly_name())
                    continue

                try:
                    await _remove_existing_device(app_context, pci_info)
                except RuntimeError as e:
                    logger.error("Failed to remove %s: %s", pci_info.friendly_name(), e)
            else:
                logger.warning("Device %s does not match any rules", pci_info.friendly_name())

def get_usb_devices(app_context):
    """Returns a list of all USB devices that match the rules from the config."""

    dev_list = []
    for device in app_context.udev_context.list_devices(subsystem="usb"):
        if not is_usb_device(device):
            continue

        dev_info = get_usb_info(device)

        if is_usb_hub(dev_info.interfaces):
            continue

        if dev_info.is_boot_device(app_context.udev_context):
            continue

        res = app_context.config.vm_for_device(dev_info)
        if res:
            # Get allowed vms or target vm
            allowed_vms = [res.target_vm] if res.target_vm else res.allowed_vms
            # Get current vm
            current_vm_name = app_context.dev_state.get_vm_for_device(dev_info)
            if app_context.dev_state.is_disconnected(dev_info):
                current_vm_name = None

            dev = dev_info.to_dict()
            dev["allowed_vms"] = allowed_vms
            dev["vm"] = current_vm_name
            dev_list.append(dev)

    return dev_list

# pylint: disable = too-many-nested-blocks
def get_pci_devices(app_context):
    """Returns a list of all PCI devices that match the rules from the config."""

    dev_list = []
    for device in app_context.udev_context.list_devices(subsystem="pci"):
        dev_info = get_pci_info(device)

        res = app_context.config.vm_for_device(dev_info)
        if res:
            # Get allowed vms or target vm
            allowed_vms = [res.target_vm] if res.target_vm else res.allowed_vms
            # Get current vm
            current_vm_name = app_context.dev_state.get_vm_for_device(dev_info)
            if app_context.dev_state.is_disconnected(dev_info):
                current_vm_name = None

            # Skip if the devices was already added as a part of IOMMU group
            if any(d.get("address") == dev_info.address for d in dev_list):
                continue

            # Check PCI devices from IOMMU group
            iommu_devs = []
            devices = get_iommu_group_devices(dev_info.address)
            if len(devices) > 1:
                if res.pci_iommu_skip_if_shared:
                    continue

                if res.pci_iommu_add_all:
                    for address in devices:
                        if address != dev_info.address:
                            pci_info = pci_info_by_address(app_context, address)
                            if pci_info:
                                iommu_devs.append(pci_info)

            # Add current device
            dev = dev_info.to_dict()
            dev["allowed_vms"] = allowed_vms
            dev["vm"] = current_vm_name
            dev_list.append(dev)

            # Add devices from the same IOMMU group
            for iommu_dev in iommu_devs:
                if any(d.get("address") == iommu_dev.address for d in dev_list):
                    continue

                dev = iommu_dev.to_dict()
                dev["allowed_vms"] = allowed_vms
                dev["vm"] = current_vm_name
                dev["iommu_member"] = True
                dev_list.append(dev)

    return dev_list

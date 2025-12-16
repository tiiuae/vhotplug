import logging
from typing import Any, TypedDict

import pyudev

from vhotplug.appcontext import AppContext
from vhotplug.config import PassthroughInfo
from vhotplug.evdev import evdev_test_grab, get_evdev_info, is_input_device
from vhotplug.pci import (
    PCIInfo,
    get_iommu_group_devices,
    get_pci_info,
    pci_info_by_address,
    pci_info_by_vid_did,
    setup_vfio,
)
from vhotplug.usb import (
    USBInfo,
    get_drivers_from_modaliases,
    get_usb_info,
    is_usb_device,
    usb_device_by_bus_port,
    usb_device_by_node,
    usb_device_by_vid_pid,
)
from vhotplug.vmm import (
    vmm_add_device,
    vmm_args_pci,
    vmm_is_pci_dev_connected,
    vmm_pause,
    vmm_remove_device,
    vmm_resume,
)

logger = logging.getLogger("vhotplug")


def log_device(device: pyudev.Device, level: int = logging.DEBUG) -> None:
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
        # for a in device.attributes.available_attributes:
        # logger.log(level, "    %s: %s", a, device.attributes.get(a))
    except AttributeError as e:
        logger.warning(e)


def _autoselect_vm(app_context: AppContext, dev_info: USBInfo | PCIInfo, allowed_vms: list[str]) -> str:
    """Select the last used VM from the state database or fall back to the first one in the list of allowed vms."""
    current_vm_name = app_context.dev_state.get_selected_vm_for_device(dev_info)
    if current_vm_name and current_vm_name in allowed_vms:
        logger.info("Selecting %s from the state database", current_vm_name)
        return current_vm_name

    logger.info("Selecting %s as first option", allowed_vms[0])
    return allowed_vms[0]


def find_vm_for_device(
    app_context: AppContext, dev_info: USBInfo | PCIInfo, check_disconnected: bool = True
) -> PassthroughInfo | None:
    """Check if device is eligible for passthrough and return passthrough info."""
    # Find a rule for the device in the config file
    res = app_context.config.vm_for_device(dev_info)
    if not res:
        logger.debug("No VM found for %s", dev_info.friendly_name())
        return None

    # Don't allow to attach device that is used to boot the system
    if dev_info.is_boot_device(app_context.udev_context):
        logger.debug("Device %s is used as a boot device", dev_info.friendly_name())
        return None

    # Check if the user manually disconnected the device before
    if check_disconnected and app_context.dev_state.is_disconnected(dev_info):
        logger.info("Device %s was permanently disconnected", dev_info.friendly_name())
        return None

    return res


async def _attach_iommu_group(app_context: AppContext, devices: list[str], vm: dict[str, str]) -> None:
    """Attaches all devices from the same IOMMU group."""
    logger.info("Adding all devices from IOMMU group (total: %s)", len(devices))

    # Attach all devices to the VM
    vm_name = vm.get("name", "")
    vm_paused = False
    try:
        for dev_addr in devices:
            pci_info = pci_info_by_address(app_context, dev_addr)
            if pci_info:
                current_vm = app_context.dev_state.get_vm_for_device(pci_info)
                if current_vm:
                    if current_vm != vm_name:
                        logger.warning(
                            "Device %s already attached to a different vm: %s (target vm: %s)",
                            pci_info.friendly_name(),
                            current_vm,
                            vm_name,
                        )
                    else:
                        continue

                if not vm_paused:
                    await vmm_pause(vm)
                    vm_paused = True
                await _attach_device_to_vm(app_context, pci_info, vm)
            else:
                logger.error("Device %s not found", dev_addr)
    finally:
        if vm_paused:
            await vmm_resume(vm)


async def attach_device(
    app_context: AppContext,
    passthrough_info: PassthroughInfo,
    dev_info: USBInfo | PCIInfo,
    ask: bool,
    vms_scope: list[str] | None = None,
) -> None:
    """Find a VM and attach a device when it is plugged in or detected at startup."""
    target_vm = passthrough_info.target_vm
    if not target_vm:
        if isinstance(dev_info, USBInfo):
            logger.info("Found multiple VMs for %s", dev_info.friendly_name())
            if ask:
                logger.info("Sending an API request to select a VM")
                if app_context.api_server:
                    app_context.api_server.notify_usb_select_vm(dev_info, passthrough_info.allowed_vms)
                return
        else:
            logger.error(
                "Multiple VMs for %s are not allowed (only supported for USB)",
                dev_info.friendly_name(),
            )

        # Try to select the last used VM from the state database or fall back to the first one in the list
        assert passthrough_info.allowed_vms is not None, "allowed_vms must be set when target_vm is None"
        target_vm = _autoselect_vm(app_context, dev_info, passthrough_info.allowed_vms)

    if vms_scope and target_vm not in vms_scope:
        logger.debug(
            "Skipping %s because its VM is %s while only devices for %s are processed",
            dev_info.friendly_name(),
            target_vm,
            vms_scope,
        )
        return

    # Get VM details from the config
    vm = app_context.config.get_vm(target_vm)
    if not vm:
        raise RuntimeError(f"VM {target_vm} is not found in the config file")

    # For PCI devices, check IOMMU group
    if isinstance(dev_info, PCIInfo):
        assert dev_info.address is not None, "PCI address cannot be None"
        devices = get_iommu_group_devices(dev_info.address)
        if len(devices) > 1:
            logger.info("Device %s has %s devices in the same IOMMU group", dev_info.friendly_name(), len(devices))
            if passthrough_info.pci_iommu_skip_if_shared:
                logger.info("Skipping device since it shares IOMMU group with other devices (total: %s)", len(devices))
                return
            if passthrough_info.pci_iommu_add_all:
                await _attach_iommu_group(app_context, devices, vm)
                return

    await _attach_device_to_vm(app_context, dev_info, vm)


async def _attach_existing_device(
    app_context: AppContext, dev_info: USBInfo | PCIInfo, selected_vm: str | None
) -> None:
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

    # Get VM details from the config
    vm = app_context.config.get_vm(target_vm)
    if not vm:
        raise RuntimeError(f"VM {target_vm} is not found in the config file")

    # Attach device to the VM
    await _attach_device_to_vm(app_context, dev_info, vm)


async def _attach_device_to_vm(app_context: AppContext, dev_info: USBInfo | PCIInfo, vm: dict[str, str]) -> None:
    """Gets VM details and attaches a device."""
    vm_name = vm.get("name", "")
    logger.info("Attaching %s to %s", dev_info.friendly_name(), vm_name)

    # Check if the device is attached to a different VM and remove
    current_vm_name = app_context.dev_state.get_vm_for_device(dev_info)
    if current_vm_name and current_vm_name != vm_name:
        logger.warning("Device is attached to %s, removing...", current_vm_name)
        try:
            await remove_device(app_context, dev_info)
        except RuntimeError as e:
            logger.warning("Failed to remove: %s", e)

    # Setup VFIO for all PCI devices in the IOMMU group if needed
    if isinstance(dev_info, PCIInfo):
        setup_vfio(dev_info)

    # Attach device to the VM
    await vmm_add_device(app_context, vm, dev_info)

    # Add selected VM to the state database
    app_context.dev_state.set_vm_for_device(dev_info, vm_name)
    app_context.dev_state.clear_disconnected(dev_info)

    if app_context.api_server:
        app_context.api_server.notify_dev_attached(dev_info, vm_name)


async def _remove_iommu_group(app_context: AppContext, devices: list[str], vm: dict[str, str]) -> None:
    """Removes all devices from the same IOMMU group."""
    logger.info("Removing devices from IOMMU group (total: %s)", len(devices))

    # Remove all devices from the VM
    vm_name = vm.get("name")
    vm_paused = False
    try:
        for dev_addr in devices:
            pci_info = pci_info_by_address(app_context, dev_addr)
            if pci_info:
                current_vm = app_context.dev_state.get_vm_for_device(pci_info)
                if current_vm and current_vm != vm_name:
                    logger.warning(
                        "Device %s in the same IOMMU group attached to a different vm: %s",
                        pci_info.friendly_name(),
                        pci_info,
                    )
                else:
                    if not vm_paused:
                        await vmm_pause(vm)
                        vm_paused = True
                    await _remove_device_from_vm(app_context, pci_info, vm)
    finally:
        if vm_paused:
            await vmm_resume(vm)


async def remove_device(app_context: AppContext, dev_info: USBInfo | PCIInfo) -> None:
    """Find a VM selected for the device and remove it."""
    # Get current VM for the device from the state database
    current_vm_name = app_context.dev_state.get_vm_for_device(dev_info)
    if not current_vm_name:
        raise RuntimeError(f"Device {dev_info.friendly_name()} is not attached to any VM")

    logger.info("Removing %s from %s", dev_info.friendly_name(), current_vm_name)

    # We can't check device rule the config here because USB devices don't have any properties on removal besides the device node
    # We can only check if the VM name is valid
    vm = app_context.config.get_vm(current_vm_name)
    if not vm:
        raise RuntimeError(f"VM {current_vm_name} not found in the configuration file")

    # For PCI devices, check IOMMU group
    if isinstance(dev_info, PCIInfo):
        assert dev_info.address is not None, "PCI address cannot be None"
        devices = get_iommu_group_devices(dev_info.address)
        if len(devices) > 1:
            logger.debug("Device %s has %s devices in the same IOMMU group", dev_info.friendly_name(), len(devices))

            # Find a rule for the device in the config file
            # TODO: save pci_iommu_add_all to the state file to avoid searching the device in config here
            res = app_context.config.vm_for_device(dev_info)
            if not res:
                raise RuntimeError(f"Device {dev_info.friendly_name()} doesn't match any rules")

            if res.pci_iommu_add_all:
                await _remove_iommu_group(app_context, devices, vm)
                return

    await _remove_device_from_vm(app_context, dev_info, vm)


async def _remove_device_from_vm(app_context: AppContext, dev_info: USBInfo | PCIInfo, vm: dict[str, str]) -> None:
    """Removes device from VM, saves its state and sends a notification."""
    # Remove from VM
    await vmm_remove_device(app_context, vm, dev_info)

    logger.info("Removed %s", dev_info.friendly_name())

    # Remove it from the state database
    app_context.dev_state.remove_vm_for_device(dev_info)

    # Send a notification
    if app_context.api_server:
        app_context.api_server.notify_dev_detached(dev_info, vm.get("name", ""))


async def _remove_existing_device(
    app_context: AppContext, dev_info: USBInfo | PCIInfo, permanent: bool = False
) -> None:
    """Remove existing device at the user's request."""
    # Remove device from the VM
    await remove_device(app_context, dev_info)

    # Mark the device as permanently disconnected
    if permanent:
        app_context.dev_state.set_disconnected(dev_info)


async def attach_existing_usb_device(app_context: AppContext, device_node: str, selected_vm: str | None) -> None:
    device = usb_device_by_node(app_context, device_node)
    if not device:
        raise RuntimeError(f"USB device {device_node} not found in the system")
    usb_info = get_usb_info(device)
    await _attach_existing_device(app_context, usb_info, selected_vm)


async def attach_existing_usb_device_by_bus_port(
    app_context: AppContext, bus: int, port: int, selected_vm: str | None
) -> None:
    device = usb_device_by_bus_port(app_context, bus, port)
    if not device:
        raise RuntimeError(f"USB device with bus {bus} and port {port} not found in the system")
    usb_info = get_usb_info(device)
    await _attach_existing_device(app_context, usb_info, selected_vm)


async def attach_existing_usb_device_by_vid_pid(
    app_context: AppContext, vid: str, pid: str, selected_vm: str | None
) -> None:
    device = usb_device_by_vid_pid(app_context, vid, pid)
    if not device:
        raise RuntimeError(f"USB device {vid}:{pid} not found in the system")
    usb_info = get_usb_info(device)
    await _attach_existing_device(app_context, usb_info, selected_vm)


async def remove_existing_usb_device(app_context: AppContext, device_node: str, permanent: bool = False) -> None:
    device = usb_device_by_node(app_context, device_node)
    if not device:
        raise RuntimeError(f"USB device {device_node} not found in the system")
    usb_info = get_usb_info(device)
    await _remove_existing_device(app_context, usb_info, permanent)


async def remove_existing_usb_device_by_bus_port(
    app_context: AppContext, bus: int, port: int, permanent: bool = False
) -> None:
    device = usb_device_by_bus_port(app_context, bus, port)
    if not device:
        raise RuntimeError(f"USB device with bus {bus} and port {port} not found in the system")
    usb_info = get_usb_info(device)
    await _remove_existing_device(app_context, usb_info, permanent)


async def remove_existing_usb_device_by_vid_pid(
    app_context: AppContext, vid: str, pid: str, permanent: bool = False
) -> None:
    device = usb_device_by_vid_pid(app_context, vid, pid)
    if not device:
        raise RuntimeError(f"USB device {vid}:{pid} not found in the system")
    usb_info = get_usb_info(device)
    await _remove_existing_device(app_context, usb_info, permanent)


async def attach_connected_usb(app_context: AppContext, vms_scope: list[str] | None = None) -> None:
    """Finds all USB devices that match the rules from the config and attaches them to VMs."""
    if vms_scope is None:
        logger.info("Attaching all USB devices")
    else:
        logger.info("Attaching USB devices for %s", vms_scope)

    for device in app_context.udev_context.list_devices(subsystem="usb"):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.debug(
                "Found USB device %s: %s",
                usb_info.friendly_name(),
                usb_info.device_node,
            )
            logger.debug(
                'Device class: "%s", subclass: "%s", protocol: "%s", interfaces: "%s"',
                usb_info.device_class,
                usb_info.device_subclass,
                usb_info.device_protocol,
                usb_info.interfaces,
            )
            drivers = get_drivers_from_modaliases(
                usb_info.get_modaliases(), app_context.config.get_modprobe(), app_context.config.get_modinfo()
            )
            for driver in drivers:
                logger.debug("Device driver: %s", driver)
            log_device(device)

            if usb_info.is_usb_hub():
                logger.debug("USB device %s is a USB hub, skipping", usb_info.friendly_name())
                continue

            res = find_vm_for_device(app_context, usb_info)
            if res:
                try:
                    await attach_device(app_context, res, usb_info, False, vms_scope)
                except RuntimeError:
                    logger.exception("Failed to attach USB device %s", usb_info.friendly_name())


async def detach_connected_usb(app_context: AppContext, vms_scope: list[str] | None = None) -> None:
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
                    logger.info(
                        "Skipping USB device %s during suspend",
                        usb_info.friendly_name(),
                    )
                    continue

                if vms_scope:
                    current_vm_name = app_context.dev_state.get_vm_for_device(usb_info)
                    if current_vm_name not in vms_scope:
                        logger.debug(
                            "Skipping USB device %s attached to %s while only devices for %s are processed",
                            usb_info.friendly_name(),
                            current_vm_name,
                            vms_scope,
                        )
                        continue
                try:
                    await _remove_existing_device(app_context, usb_info)
                except RuntimeError:
                    logger.exception("Failed to remove %s", usb_info.friendly_name())
            else:
                logger.warning("Device %s does not match any rules", usb_info.friendly_name())


async def attach_existing_pci_device(app_context: AppContext, pci_address: str, selected_vm: str | None) -> None:
    """Find PCI device by address and attach to selected VM."""
    pci_info = pci_info_by_address(app_context, pci_address)
    if not pci_info:
        raise RuntimeError(f"PCI device {pci_address} not found in the system")
    await _attach_existing_device(app_context, pci_info, selected_vm)


async def attach_existing_pci_device_by_vid_did(
    app_context: AppContext, vid: str, did: str, selected_vm: str | None
) -> bool:
    """Find PCI device by vendor ID and device ID and attach to selected VM."""
    pci_info = pci_info_by_vid_did(app_context, int(vid, 16), int(did, 16))
    if not pci_info:
        raise RuntimeError(f"PCI device {vid}:{did} not found in the system")
    await _attach_existing_device(app_context, pci_info, selected_vm)
    return True


async def remove_existing_pci_device(app_context: AppContext, pci_address: str, permanent: bool = False) -> None:
    """Find PCI device by address and detach from VM."""
    pci_info = pci_info_by_address(app_context, pci_address)
    if not pci_info:
        raise RuntimeError(f"PCI device {pci_address} not found in the system")
    await _remove_existing_device(app_context, pci_info, permanent)


async def remove_existing_pci_device_by_vid_did(
    app_context: AppContext, vid: str, did: str, permanent: bool = False
) -> bool:
    """Find PCI device by vendor ID and device ID and detach from VM."""
    pci_info = pci_info_by_vid_did(app_context, int(vid, 16), int(did, 16))
    if not pci_info:
        raise RuntimeError(f"PCI device {vid}:{did} not found in the system")
    await _remove_existing_device(app_context, pci_info, permanent)
    return True


def _get_pci_devices(
    app_context: AppContext, disconnected: bool | None = None, check_iommu_group: bool = True
) -> list[Any]:
    """Finds all PCI devices that match the rules from the config."""

    class Device(TypedDict):
        pci_info: PCIInfo
        passthrough_info: PassthroughInfo
        current_vm: str | None
        iommu_member: bool

    devices: list[Device] = []

    # Find PCI devices eligible for passthrough
    for device in app_context.udev_context.list_devices(subsystem="pci"):
        pci_info = get_pci_info(device)
        logger.debug("Found PCI device %s: %s", pci_info.friendly_name(), pci_info.address)
        logger.debug(
            'PCI class: "%s", subclass: "%s", prog if: "%s", driver: "%s"',
            pci_info.pci_class,
            pci_info.pci_subclass,
            pci_info.pci_prog_if,
            pci_info.driver,
        )
        log_device(device)

        passthrough_info = find_vm_for_device(app_context, pci_info, False)
        if passthrough_info is None:
            continue

        # Get current vm
        current_vm_name = app_context.dev_state.get_vm_for_device(pci_info)
        if app_context.dev_state.is_disconnected(pci_info):
            logger.info("Device %s was permanently disconnected", pci_info.friendly_name())
            current_vm_name = None
            if disconnected is not None and disconnected is False:
                continue
        elif disconnected is not None and disconnected is True:
            continue

        # Skip if the devices was already added as a part of IOMMU group
        if any(d["pci_info"].address == pci_info.address for d in devices):
            continue

        # Check PCI devices from IOMMU group
        if check_iommu_group:
            iommu_devs = []
            iommu_devs_addr = get_iommu_group_devices(pci_info.address)
            if len(iommu_devs_addr) > 1:
                # Skip the device when there are other devices in the same IOMMU group
                if passthrough_info.pci_iommu_skip_if_shared:
                    continue

                # Find devices from the same IOMMU group
                if passthrough_info.pci_iommu_add_all:
                    for address in iommu_devs_addr:
                        if address != pci_info.address:
                            iommu_pci_info = pci_info_by_address(app_context, address)
                            if iommu_pci_info:
                                iommu_devs.append(iommu_pci_info)

                # Add devices from the same IOMMU group
                for iommu_dev in iommu_devs:
                    if any(d["pci_info"].address == iommu_dev.address for d in devices):
                        continue

                    devices.append(
                        {
                            "pci_info": iommu_dev,
                            "passthrough_info": passthrough_info,
                            "current_vm": current_vm_name,
                            "iommu_member": True,
                        }
                    )

        devices.append(
            {
                "pci_info": pci_info,
                "passthrough_info": passthrough_info,
                "current_vm": current_vm_name,
                "iommu_member": False,
            }
        )

    # Sort by order
    devices.sort(key=lambda x: x["passthrough_info"].order)
    return devices


async def attach_connected_pci(app_context: AppContext, vms_scope: list[str] | None = None) -> None:
    """Finds all PCI devices that match the rules from the config and attaches them to VMs."""
    if vms_scope is None:
        logger.info("Attaching all PCI devices")
    else:
        logger.info("Attaching PCI devices for %s", vms_scope)

    # Get a list of all PCI devices for passthrough but do not include devices from IOMMU group
    # They are checked and attached atomically in the attach_device function with VM pause/resume
    devices = _get_pci_devices(app_context, False, False)

    # Attach to VMs
    for device in devices:
        try:
            await attach_device(app_context, device["passthrough_info"], device["pci_info"], False, vms_scope)
        except RuntimeError:
            logger.exception("Failed to attach PCI device %s", device["pci_info"].friendly_name())


async def detach_connected_pci(app_context: AppContext, vms_scope: list[str] | None = None) -> None:
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
                        logger.debug(
                            "Skipping PCI device %s attached to %s while only devices for %s are processed",
                            pci_info.friendly_name(),
                            current_vm_name,
                            vms_scope,
                        )
                        continue

                if res.skip_on_suspend:
                    logger.info(
                        "Skipping PCI device %s during suspend",
                        pci_info.friendly_name(),
                    )
                    continue

                try:
                    await _remove_existing_device(app_context, pci_info)
                except RuntimeError:
                    logger.exception("Failed to remove %s", pci_info.friendly_name())
            else:
                # That's normal for IOMMU group members when pciIommuAddAll is enabled
                logger.debug("Device %s does not match any rules", pci_info.friendly_name())


async def detach_disconnected_pci(app_context: AppContext, vms_scope: list[str] | None = None) -> None:
    """Detach all PCI devices that were previously permanently disconnected."""
    logger.info("Checking permanently disconnected PCI devices")

    for device in app_context.udev_context.list_devices(subsystem="pci"):
        pci_info = get_pci_info(device)
        if app_context.dev_state.is_disconnected(pci_info):
            logger.info("Found permanently disconnected device: %s", pci_info.friendly_name())

            # Find a rule for the device in the config file
            res = app_context.config.vm_for_device(pci_info)
            if not res:
                logger.warning("No rule for %s", pci_info.friendly_name())
                continue

            # For PCI devices there must be single target vm defined
            if not res.target_vm:
                logger.warning("Target VM is not set for %s", pci_info.friendly_name())
                continue

            if vms_scope and res.target_vm not in vms_scope:
                continue

            try:
                # Check if the VM is valid
                vm = app_context.config.get_vm(res.target_vm)
                if not vm:
                    logger.warning("VM %s not found in the configuration file", res.target_vm)
                elif await vmm_is_pci_dev_connected(vm, pci_info):
                    logger.info("Detaching %s from %s", pci_info.friendly_name(), res.target_vm)
                    await vmm_remove_device(app_context, vm, pci_info)
            except RuntimeError:
                logger.exception("Failed to remove %s", pci_info.friendly_name())


def get_usb_device_list(app_context: AppContext, disconnected: bool) -> list[dict[str, Any]]:
    """Returns a list of all USB devices that match the rules from the config."""
    dev_list: list[dict[str, Any]] = []
    for device in app_context.udev_context.list_devices(subsystem="usb"):
        if not is_usb_device(device):
            continue

        usb_info = get_usb_info(device)

        if usb_info.is_usb_hub():
            continue

        if usb_info.is_boot_device(app_context.udev_context):
            continue

        res = app_context.config.vm_for_device(usb_info)
        if res:
            # Get allowed vms or target vm
            allowed_vms = [res.target_vm] if res.target_vm else res.allowed_vms
            # Get current vm
            current_vm_name = app_context.dev_state.get_vm_for_device(usb_info)
            if app_context.dev_state.is_disconnected(usb_info):
                current_vm_name = None
            elif disconnected:
                continue

            dev = usb_info.to_dict()
            dev["allowed_vms"] = allowed_vms
            dev["vm"] = current_vm_name
            dev_list.append(dev)

    return dev_list


def get_pci_device_list(app_context: AppContext, disconnected: bool) -> list[dict[str, Any]]:
    """Returns a list of all PCI devices that match the rules from the config."""
    # Find PCI devices eligible for passthrough
    devices = _get_pci_devices(app_context, disconnected, True)

    # Add to the list
    dev_list: list[dict[str, Any]] = []
    for device in devices:
        dev_info = device["pci_info"]

        # Convert device to dict and add extra fields
        dev = dev_info.to_dict()

        if device["iommu_member"]:
            dev["iommu_member"] = True

        # Use the target vm when no allowed vms are defined
        passthrough_info = device["passthrough_info"]
        allowed_vms = [passthrough_info.target_vm] if passthrough_info.target_vm else passthrough_info.allowed_vms
        dev["allowed_vms"] = allowed_vms
        dev["vm"] = device["current_vm"]
        dev_list.append(dev)

    return dev_list


async def attach_connected_evdev(app_context: AppContext) -> None:
    """Finds non-USB evdev devices and attaches them to the selected VM."""
    logger.info("Checking connected non-USB input devices")
    for device in app_context.udev_context.list_devices(subsystem="input"):
        bus = device.properties.get("ID_BUS")
        if is_input_device(device) and bus != "usb":
            evdev_info = get_evdev_info(device)
            logger.info(
                "Found event device: %s, bus: %s, path tag: %s",
                evdev_info.friendly_name(),
                evdev_info.bus,
                evdev_info.path_tag,
            )
            log_device(device, logging.DEBUG)

            # Find a rule for the device in the config file
            res = app_context.config.vm_for_device(evdev_info)
            if not res:
                logger.debug("No VM found for %s", evdev_info.friendly_name())
                continue

            if not res.target_vm:
                logger.error("Target VM is not defined for %s", evdev_info.friendly_name())
                continue

            # Get VM details from the config
            vm = app_context.config.get_vm(res.target_vm)
            if not vm:
                logger.error("VM %s is not found in the config file", res.target_vm)
                continue

            if await evdev_test_grab(device):
                logger.info(
                    "Device %s is grabbed by another process, it is likely already connected to the VM",
                    evdev_info.friendly_name(),
                )
            else:
                logger.info("Attaching %s to %s", evdev_info.friendly_name(), res.target_vm)
                try:
                    await vmm_add_device(app_context, vm, evdev_info)
                except RuntimeError:
                    logger.exception("Failed to attach evdev device %s", evdev_info.friendly_name())


def get_pci_vmm_args(app_context: AppContext, vm_name: str, qemu_bus_prefix: str | None) -> list[str]:
    """Returns a list of VMM arguments for all PCI devices that match the rules from the config."""
    # Get VM details from the config
    vm = app_context.config.get_vm(vm_name)
    if not vm:
        raise RuntimeError(f"VM {vm_name} is not found in the config file")
    # Get all PCI devices that match the rules in the config
    args: list[str] = []
    devs = _get_pci_devices(app_context, None, True)
    dev_number = 0
    for dev in devs:
        # Filter by target VM
        passthrough_info = dev["passthrough_info"]
        if passthrough_info.target_vm != vm_name:
            continue

        pci_info = dev["pci_info"]

        # Setup VFIO for all PCI devices in the IOMMU group
        setup_vfio(pci_info)

        # Generate arguments fo the VMM
        dev_args = vmm_args_pci(vm, pci_info, dev_number, qemu_bus_prefix)
        dev_number = dev_number + 1
        args.extend(dev_args)

    logger.info("VMM args: %s", args)
    return args

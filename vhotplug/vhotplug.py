import argparse
import asyncio
import logging
import os

import pyudev

from vhotplug.apiserver import APIServer
from vhotplug.appcontext import AppContext
from vhotplug.config import Config
from vhotplug.device import (
    attach_connected_evdev,
    attach_connected_pci,
    attach_connected_usb,
    attach_device,
    deauthorize_unmatched_usb,
    detach_disconnected_pci,
    find_vm_for_device,
    get_usb_info,
    is_usb_device,
    log_detected_devices,
    log_device,
    remove_device,
)
from vhotplug.devicestate import DeviceState
from vhotplug.filewatcher import FileWatcher
from vhotplug.pci import check_vfio
from vhotplug.usb import (
    authorize_usb_device,
    authorize_usb_hubs,
    get_drivers_from_modaliases,
    get_usb_authorized_default,
    get_usb_device_authorization,
    set_usb_authorized_default,
)

logger = logging.getLogger("vhotplug")


async def device_event(app_context: AppContext, device: pyudev.Device) -> None:
    if device.action == "add":
        logger.debug("Device plugged: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        log_device(device)
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            if usb_info.is_usb_hub() and app_context.config.usb_authorization_enabled():
                if get_usb_device_authorization(usb_info) != 1:
                    logger.info("Authorizing USB hub %s", usb_info.friendly_name())
                    authorize_usb_device(usb_info)
                return

            logger.info(
                "USB device %s connected: %s",
                usb_info.friendly_name(),
                device.device_node,
            )
            logger.info(
                'Device class: "%s", subclass: "%s", protocol: "%s", interfaces: "%s", removable: "%s"',
                usb_info.device_class,
                usb_info.device_subclass,
                usb_info.device_protocol,
                usb_info.interfaces,
                usb_info.removable,
            )

            drivers = get_drivers_from_modaliases(
                usb_info.get_modaliases(), app_context.config.get_modprobe(), app_context.config.get_modinfo()
            )
            for driver in drivers:
                logger.info("Device driver: %s", driver)

            logger.info("Device authorization: %s", get_usb_device_authorization(usb_info))

            # Notify that USB device is connected to host
            if app_context.api_server:
                app_context.api_server.notify_usb_connected(usb_info)

            try:
                res = find_vm_for_device(app_context, usb_info)
                if res:
                    await attach_device(app_context, res, usb_info, True)
                elif app_context.config.usb_authorization_host_allowed(usb_info):
                    logger.info("USB device %s is allowed on host, authorizing", usb_info.friendly_name())
                    authorize_usb_device(usb_info)
            except RuntimeError:
                logger.exception("Failed to attach device %s", device.device_node)

    elif device.action == "remove":
        logger.debug("Device unplugged: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        log_device(device)
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.info("USB device disconnected: %s", device.device_node)
            try:
                await remove_device(app_context, usb_info)
            except RuntimeError:
                logger.exception("Failed to detach device %s", device.device_node)

            # Notify that USB device is disconnected from host
            if app_context.api_server:
                app_context.api_server.notify_usb_disconnected(usb_info)

    elif device.action == "change":
        logger.debug("Device changed: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        if device.subsystem == "power_supply":
            logger.info(
                "Power supply device %s changed, this may indicate a system resume",
                device.sys_name,
            )


async def monitor_loop(app_context: AppContext, file_watcher: FileWatcher, attach_connected: bool) -> None:
    while True:
        device = await asyncio.to_thread(app_context.udev_monitor.poll, 1)
        if device:
            await device_event(app_context, device)

        # Detect if any VMs restarted
        vm_restart_detected, sockets_restarted = file_watcher.detect_restart()
        if vm_restart_detected and attach_connected:
            vms_restarted: list[str] = []
            for sock in sockets_restarted:
                vm = app_context.config.get_vm_by_socket(sock)
                if vm:
                    vm_name = vm.get("name")
                    if vm_name:
                        vms_restarted.append(vm_name)

            app_context.dev_state.clear_crosvm_usb_ports(vms_restarted)

            # Check non-USB evdev devices for restarted VMs
            await attach_connected_evdev(app_context)
            # Check PCI devices for restarted VMs
            await attach_connected_pci(app_context, vms_restarted)
            # Check PCI devices for restarted VMs and detach those that were previously permanently detached
            await detach_disconnected_pci(app_context, vms_restarted)
            # Check USB devices for restarted VMs
            await attach_connected_usb(app_context, vms_restarted)


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Hot-plugging USB devices to the virtual machines")
    parser.add_argument("-c", "--config", type=str, required=True, help="Path to the configuration file")
    parser.add_argument(
        "-a",
        "--attach-connected",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Attach connected devices on startup",
    )
    parser.add_argument(
        "-d",
        "--debug",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Enable debug messages",
    )
    args = parser.parse_args()

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if args.debug else logging.INFO)

    if not os.path.exists(args.config):  # noqa: ASYNC240
        logger.error("Configuration file %s not found", args.config)
        return

    check_vfio()

    config = Config(args.config)

    udev_context = pyudev.Context()
    udev_monitor = pyudev.Monitor.from_netlink(udev_context)

    file_watcher = FileWatcher()
    for vm in config.get_all_vms():
        vm_socket = vm.get("socket")
        if vm_socket:
            file_watcher.add_file(vm_socket)

    dev_state = DeviceState(config.persistency_enabled(), config.state_path())

    app_context = AppContext(config, udev_monitor, udev_context, dev_state)

    logger.info("Usbcore authorized_default: %s", get_usb_authorized_default())
    if config.usb_authorization_enabled():
        logger.info("Enabling USB authorization")
        set_usb_authorized_default(0)
        authorize_usb_hubs(udev_context)

    api_server = None
    if config.api_enabled():
        api_server = APIServer(app_context, asyncio.get_running_loop())
        app_context.api_server = api_server
        api_server.start()

    # Log all detected devices on startup
    log_detected_devices(app_context)

    if args.attach_connected:
        # Check all evdev input devices devices and attach to VMs
        await attach_connected_evdev(app_context)
        # Check all USB devices devices and attach to VMs
        await attach_connected_usb(app_context)
        # Check all PCI devices devices and attach to VMs
        await attach_connected_pci(app_context)
        # Check all PCI devices and detach those that were previously permanently detached
        await detach_disconnected_pci(app_context)

    # Deauthorize all unmatched USB devices except USB root hubs and the boot device
    if config.usb_authorization_deauthorize_unmatched():
        deauthorize_unmatched_usb(app_context)

    logger.info("Waiting for new devices")
    await monitor_loop(app_context, file_watcher, args.attach_connected)


def main() -> None:
    try:
        asyncio.run(async_main())
    except asyncio.CancelledError:
        logger.info("Cancelled by event loop")
    except KeyboardInterrupt:
        logger.info("Ctrl+C pressed")
    logger.info("Exiting")

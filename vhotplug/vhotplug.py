import logging
import asyncio
import argparse
import os
from dataclasses import dataclass
from typing import Optional
import pyudev
from vhotplug.device import attach_usb_device, remove_usb_device, log_device, is_usb_device, get_usb_info, attach_connected_evdev, attach_connected_devices
from vhotplug.config import Config
from vhotplug.filewatcher import FileWatcher
from vhotplug.apiserver import APIServer
from vhotplug.usbstate import USBState

logger = logging.getLogger("vhotplug")

@dataclass
class AppContext:
    config: object
    udev_monitor: object
    udev_context: object
    usb_state: object
    api_server: Optional[object] = None

async def device_event(app_context, device):
    if device.action == 'add':
        logger.debug("Device plugged: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        log_device(device)
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.info("USB device %s connected: %s", usb_info.friendly_name(), device.device_node)
            logger.info('Device class: "%s", subclass: "%s", protocol: "%s", interfaces: "%s"', usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol, usb_info.interfaces)
            try:
                await attach_usb_device(app_context, usb_info, True)
            except RuntimeError as e:
                logger.error("Failed to attach device %s: %s", device.device_node, e)
    elif device.action == 'remove':
        logger.debug("Device unplugged: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        log_device(device)
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.info("USB device disconnected: %s", device.device_node)
            try:
                await remove_usb_device(app_context, usb_info)
            except RuntimeError as e:
                logger.error("Failed to detach device %s: %s", device.device_node, e)
    elif device.action == 'change':
        logger.debug("Device changed: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        if device.subsystem == 'power_supply':
            logger.info("Power supply device %s changed, this may indicate a system resume", device.sys_name)

async def monitor_loop(app_context, file_watcher, attach_connected):
    while True:
        device = await asyncio.to_thread(app_context.udev_monitor.poll, 1)
        if device:
            await device_event(app_context, device)

        # Check all devices because one or more VMs have restarted
        vm_restart_detected, vms_restarted = file_watcher.detect_restart()
        if vm_restart_detected and attach_connected:
            # Check non-USB evdev devices when the target VM is restarted
            vm, _ = app_context.config.vm_for_evdev_devices()
            if vm and vm.get("socket") in vms_restarted:
                await attach_connected_evdev(app_context)
            # Check all USB devices
            await attach_connected_devices(app_context)

async def async_main():
    parser = argparse.ArgumentParser(description="Hot-plugging USB devices to the virtual machines")
    parser.add_argument("-c", "--config", type=str, required=True, help="Path to the configuration file")
    parser.add_argument("-a", "--attach-connected", default=False, action=argparse.BooleanOptionalAction, help="Attach connected devices on startup")
    parser.add_argument("-d", "--debug", default=False, action=argparse.BooleanOptionalAction, help="Enable debug messages")
    args = parser.parse_args()

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if args.debug else logging.INFO)

    if not os.path.exists(args.config):
        logger.error("Configuration file %s not found", args.config)
        return

    config = Config(args.config)

    udev_context = pyudev.Context()
    udev_monitor = pyudev.Monitor.from_netlink(udev_context)

    file_watcher = FileWatcher()
    for vm in config.get_all_vms():
        vm_socket = vm.get("socket")
        if vm_socket:
            file_watcher.add_file(vm_socket)

    usb_state = USBState(config.persistency_enabled(), config.state_path())

    app_context = AppContext(config, udev_monitor, udev_context, usb_state)

    api_server = None
    if config.api_enabled():
        api_server = APIServer(app_context, asyncio.get_event_loop())
        app_context.api_server = api_server
        api_server.start()

    if args.attach_connected:
        # Check all evdev input devices devices and attach to VMs
        await attach_connected_evdev(app_context)
        # Check all USB devices devices and attach to VMs
        await attach_connected_devices(app_context)

    logger.info("Waiting for new devices")
    await monitor_loop(app_context, file_watcher, args.attach_connected)

def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(async_main())
    except asyncio.CancelledError:
        logger.info("Cancelled by event loop")
    except KeyboardInterrupt:
        logger.info("Ctrl+C pressed")
    logger.info("Exiting")

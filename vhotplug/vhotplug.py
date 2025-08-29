import logging
import asyncio
import argparse
import os
import pyudev
from vhotplug.device import attach_usb_device, remove_usb_device, log_device, is_usb_device, get_usb_info, attach_connected_devices
from vhotplug.config import Config
from vhotplug.filewatcher import FileWatcher

logger = logging.getLogger("vhotplug")

async def device_event(context, config, device):
    if device.action == 'add':
        logger.debug("Device plugged: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        log_device(device)
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.info("USB device %s:%s (%s %s) connected: %s", usb_info.vid, usb_info.pid, usb_info.vendor_name, usb_info.product_name, device.device_node)
            logger.info('Device class: "%s", subclass: "%s", protocol: "%s", interfaces: "%s"', usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol, usb_info.interfaces)
            await attach_usb_device(context, config, device)
    elif device.action == 'remove':
        logger.debug("Device unplugged: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        log_device(device)
        if is_usb_device(device):
            logger.info("USB device disconnected: %s", device.device_node)
            await remove_usb_device(config, device)
    elif device.action == 'change':
        logger.debug("Device changed: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        if device.subsystem == 'power_supply':
            logger.info("Power supply device %s changed, this may indicate a system resume", device.sys_name)

async def async_main():
    parser = argparse.ArgumentParser(description="Hot-plugging USB devices to the virtual machines")
    parser.add_argument("-c", "--config", type=str, required=True, help="Path to the configuration file")
    parser.add_argument("-a", "--attach-connected", default=False, action=argparse.BooleanOptionalAction, help="Attach connected devices on startup")
    parser.add_argument("-d", "--debug", default=False, action=argparse.BooleanOptionalAction, help="Enable debug messages")
    args = parser.parse_args()

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    if args.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if not os.path.exists(args.config):
        logger.error("Configuration file %s not found", args.config)
        return

    config = Config(args.config)

    context = pyudev.Context()
    if args.attach_connected:
        await attach_connected_devices(context, config)

    monitor = pyudev.Monitor.from_netlink(context)

    watcher = FileWatcher()
    for vm in config.get_all_vms():
        vm_socket = vm.get("socket")
        if vm_socket:
            watcher.add_file(vm_socket)

    logger.info("Waiting for new devices")
    try:
        while True:
            device = monitor.poll(timeout=1)
            if device is not None:
                await device_event(context, config, device)
            if watcher.detect_restart() and args.attach_connected:
                await attach_connected_devices(context, config)

    except KeyboardInterrupt:
        logger.info("Ctrl+C")

    logger.info("Exiting")

def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(async_main())

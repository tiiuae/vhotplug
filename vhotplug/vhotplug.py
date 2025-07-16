import pyudev
import logging
import asyncio
import argparse
import os
from vhotplug.device import attach_usb_device, remove_usb_device, log_device, is_usb_device, get_usb_info, attach_connected_devices
from vhotplug.config import Config
from vhotplug.filewatcher import FileWatcher

logger = logging.getLogger("vhotplug")

async def device_event(context, config, device):
    if device.action == 'add':
        logger.debug(f"Device plugged: {device.sys_name}.")
        logger.debug(f"Subsystem: {device.subsystem}, path: {device.device_path}")
        log_device(device)
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.info(f"USB device {usb_info.vid}:{usb_info.pid} ({usb_info.vendor_name} {usb_info.product_name}) connected: {device.device_node}")
            logger.info(f'Device class: "{usb_info.device_class}", subclass: "{usb_info.device_subclass}", protocol: "{usb_info.device_protocol}", interfaces: "{usb_info.interfaces}"')
            await attach_usb_device(context, config, device)
    elif device.action == 'remove':
        logger.debug(f"Device unplugged: {device.sys_name}.")
        logger.debug(f"Subsystem: {device.subsystem}, path: {device.device_path}")
        log_device(device)
        if is_usb_device(device):
            logger.info(f"USB device disconnected: {device.device_node}")
            await remove_usb_device(config, device)
    elif device.action == 'change':
        logger.debug(f"Device changed: {device.sys_name}.")
        logger.debug(f"Subsystem: {device.subsystem}, path: {device.device_path}")
        if device.subsystem == 'power_supply':
            logger.info(f"Power supply device {device.sys_name} changed, this may indicate a system resume")

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
        logger.error(f"Configuration file {args.config} not found")
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
            if device != None:
                await device_event(context, config, device)
            if watcher.detect_restart() == True and args.attach_connected:
                    await attach_connected_devices(context, config)

    except KeyboardInterrupt:
        logger.info("Ctrl+C")

    logger.info("Exiting")

def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(async_main())

import pyudev
import logging
import asyncio
import argparse
import os
from vhotplug.qemulink import *
from vhotplug.device import *
from vhotplug.config import *
from vhotplug.filewatcher import *

logger = logging.getLogger("vhotplug")

async def device_event(context, config, device):
    if device.action == 'add':
        logger.debug(f"Device plugged: {device.sys_name}.")
        logger.debug(f"Subsystem: {device.subsystem}, path: {device.device_path}")
        log_device(device)
        if is_usb_device(device):
            vid, pid, vendor_name, product_name, interfaces = get_usb_info(device)
            logger.info(f"USB device {vid}:{pid} connected: {device.device_node}")
            logger.info(f'Vendor: "{vendor_name}", product: "{product_name}", interfaces: "{interfaces}"')
            await attach_usb_device(context, config, device)
    elif device.action == 'remove':
        logger.debug(f"Device unplugged: {device.sys_name}.")
        logger.debug(f"Subsystem: {device.subsystem}, path: {device.device_path}")
        log_device(device)
        if is_usb_device(device):
            logger.info(f"USB device disconnected: {device.device_node}")
            await remove_usb_device(config, device)

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
        qmp_socket = vm.get("qmpSocket")
        watcher.add_file(qmp_socket)

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

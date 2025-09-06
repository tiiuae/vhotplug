import logging
import asyncio
import argparse
import os
import pyudev
from vhotplug.device import vm_for_usb_device, attach_usb_device, remove_usb_device, log_device, is_usb_device, get_usb_info, attach_connected_devices
from vhotplug.config import Config
from vhotplug.filewatcher import FileWatcher
from vhotplug.apiserver import APIServer

logger = logging.getLogger("vhotplug")

async def device_event(context, config, device, api_server):
    if device.action == 'add':
        logger.debug("Device plugged: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        log_device(device)
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.info("USB device %s:%s (%s %s) connected: %s", usb_info.vid, usb_info.pid, usb_info.vendor_name, usb_info.product_name, device.device_node)
            logger.info('Device class: "%s", subclass: "%s", protocol: "%s", interfaces: "%s"', usb_info.device_class, usb_info.device_subclass, usb_info.device_protocol, usb_info.interfaces)
            try:
                vm = await vm_for_usb_device(context, config, api_server, usb_info, None, True)
                if vm:
                    await attach_usb_device(config, api_server, usb_info, vm)
            except RuntimeError as e:
                logger.error("Failed to attach device: %s", e)
    elif device.action == 'remove':
        logger.debug("Device unplugged: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        log_device(device)
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            logger.info("USB device disconnected: %s", device.device_node)
            try:
                await remove_usb_device(config, usb_info, api_server)
            except RuntimeError as e:
                logger.error("Failed to detach device: %s", e)
    elif device.action == 'change':
        logger.debug("Device changed: %s", device.sys_name)
        logger.debug("Subsystem: %s, path: %s", device.subsystem, device.device_path)
        if device.subsystem == 'power_supply':
            logger.info("Power supply device %s changed, this may indicate a system resume", device.sys_name)

# pylint: disable = too-many-positional-arguments
async def monitor_loop(monitor, context, config, api_server, watcher, attach_connected):
    while True:
        device = await asyncio.to_thread(monitor.poll, 1)
        if device:
            await device_event(context, config, device, api_server)

        if watcher.detect_restart() and attach_connected:
            await attach_connected_devices(context, config)

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

    context = pyudev.Context()
    if args.attach_connected:
        await attach_connected_devices(context, config)

    monitor = pyudev.Monitor.from_netlink(context)

    watcher = FileWatcher()
    for vm in config.get_all_vms():
        vm_socket = vm.get("socket")
        if vm_socket:
            watcher.add_file(vm_socket)

    api_server = None
    if config.api_enabled():
        api_server = APIServer(config, context, asyncio.get_event_loop())
        api_server.start()

    logger.info("Waiting for new devices")
    await monitor_loop(monitor, context, config, api_server, watcher, args.attach_connected)

def main():
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(async_main())
    except asyncio.CancelledError:
        logger.info("Cancelled by event loop")
    except KeyboardInterrupt:
        logger.info("Ctrl+C pressed")
    logger.info("Exiting")

#!/usr/bin/env python

import pyudev
import logging
import asyncio
import argparse
from qemulink import *
from device import *

logger = logging.getLogger("vhotplug")

async def device_event(qmpinput, qmpsound, qmpdisk, qmpnet, device):
    if device.action == 'add':
        logger.info(f"Device plugged. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
        log_device(device)
        if qmpinput and is_input_device(device):
            await add_usb_device(qmpinput, device)
        elif qmpsound and is_sound_device(device):
            await add_usb_device(qmpsound, device)
        elif qmpdisk and is_disk_device(device):
            await add_usb_device(qmpdisk, device)
        elif qmpnet and is_network_device(device):
            await add_usb_device(qmpnet, device)
    elif device.action == 'remove':
        logger.info(f"Device unplugged. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
        log_device(device)
        if device.subsystem == "usb" and device.device_type == "usb_device":
            await remove_device(qmpinput, device)
            await remove_device(qmpsound, device)
            await remove_device(qmpdisk, device)
            await remove_device(qmpnet, device)

async def main():
    parser = argparse.ArgumentParser(description="Hot-plugging USB devices to the virtual machines")
    parser.add_argument("--add-connected", default=False, action=argparse.BooleanOptionalAction, help="Add already connected devices on startup")
    parser.add_argument("--add-evdev", default=False, action=argparse.BooleanOptionalAction, help="Add non-USB input devices using evdev passthrough")
    parser.add_argument("--pcie-bus-prefix", type=str, required=False, dest="busprefix", help="PCIe bus prefix for evdev passthrough")
    parser.add_argument("--qmp-input", type=str, required=False, dest="qmpinput", help="Enable hot-plugging of input devices using the specified QMP socket")
    parser.add_argument("--qmp-sound", type=str, required=False, dest="qmpsound", help="Enable hot-plugging of sound devices using the specified QMP socket")
    parser.add_argument("--qmp-disk", type=str, required=False, dest="qmpdisk", help="Enable hot-plugging of disk devices using the specified QMP socket")
    parser.add_argument("--qmp-net", type=str, required=False, dest="qmpnet", help="Enable hot-plugging of network devices using the specified QMP socket")
    parser.add_argument("-d", "--debug", default=False, action=argparse.BooleanOptionalAction, help="Enable debug messages")
    args = parser.parse_args()

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    if args.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if args.qmpinput:
        logger.info(f"Connecting to {args.qmpinput}")
        qemuinput = QEMULink(args.qmpinput)
        await qemuinput.wait_for_vm()
        await qemuinput.query_pci()
        await qemuinput.usb()
        await qemuinput.usbhost()

    context = pyudev.Context()
    if args.add_connected:
        logger.info("Adding connected devices")
        await add_connected_devices(context, args.qmpinput, args.qmpsound, args.qmpdisk, args.qmpnet, args.add_evdev, args.busprefix)

    monitor = pyudev.Monitor.from_netlink(context)

    logger.info("Waiting for a device")
    try:
        while True:
            device = monitor.poll(timeout=1)
            if device != None:
                await device_event(args.qmpinput, args.qmpsound, args.qmpdisk, args.qmpnet, device)
    except KeyboardInterrupt:
        logger.info("Ctrl+C")

    logger.info("Exiting")

if __name__ ==  '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

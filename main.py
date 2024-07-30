#!/usr/bin/env python

import pyudev
import logging
import asyncio
import argparse
from qemulink import *
from device import *

logger = logging.getLogger("vhotplug")

async def device_event(qmpinput, qmpsound, device):
    if device.action == 'add':
        logger.info(f"Device plugged. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
        log_device(device)
        if is_input_device(device):
            await add_usb_device(qmpinput, device)
        elif qmpsound and is_sound_device(device):
            await add_usb_device(qmpsound, device)
    elif device.action == 'remove':
        logger.info(f"Device unplugged. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
        log_device(device)
        if device.subsystem == "usb" and device.device_type == "usb_device":
            await remove_device(qmpinput, device)
            await remove_device(qmpsound, device)

async def main():
    parser = argparse.ArgumentParser(description="Hot-plugging USB devices to the virtual machines")
    parser.add_argument("--add-connected", default=False, action=argparse.BooleanOptionalAction, help="Add already connected devices on startup")
    parser.add_argument("--add-evdev", default=False, action=argparse.BooleanOptionalAction, help="Add non-USB input devices using evdev passthrough")
    parser.add_argument("--pcie-bus-prefix", type=str, required=False, dest="busprefix", help="PCIe bus prefix for evdev passthrough")
    parser.add_argument("--qmp-input", type=str, required=True, dest="qmpinput", help="Path to the QMP socket of a VM for input devices")
    parser.add_argument("--qmp-sound", type=str, required=False, dest="qmpsound", help="Path to the QMP socket of a VM for sound devices")
    parser.add_argument("-d", "--debug", default=False, action=argparse.BooleanOptionalAction, help="Enable debug messages")
    args = parser.parse_args()

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    if args.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    logger.info(f"Connecting to {args.qmpinput}")
    qemuinput = QEMULink(args.qmpinput)
    await qemuinput.wait_for_vm()
    await qemuinput.query_pci()
    await qemuinput.usb()
    await qemuinput.usbhost()

    if args.qmpsound:
        logger.info(f"Connecting to {args.qmpsound}")
        qemusound = QEMULink(args.qmpsound)
        await qemusound.wait_for_vm()

    context = pyudev.Context()
    if args.add_connected:
        logger.info("Adding connected devices")
        await add_connected_devices(args.qmpinput, args.qmpsound, context, args.add_evdev, args.busprefix)

    monitor = pyudev.Monitor.from_netlink(context)

    logger.info("Waiting for a device")
    try:
        while True:
            device = monitor.poll(timeout=1)
            if device != None:
                await device_event(args.qmpinput, args.qmpsound, device)
    except KeyboardInterrupt:
        logger.info("Ctrl+C")

    logger.info("Exiting")

if __name__ ==  '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

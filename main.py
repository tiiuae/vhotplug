#!/usr/bin/env python

import pyudev
import logging
import asyncio
import argparse
from qemulink import *
from device import *

logger = logging.getLogger("vhotplug")

async def device_event(qmpsock, device):
    if device.action == 'add':
        logger.info(f"Device plugged. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
        if device.subsystem == "input" and device.sys_name.startswith("event"):
            if device.properties.get("ID_INPUT") == "1":
                await add_device(qmpsock, device)
    elif device.action == 'remove':
        logger.info(f"Device unplugged. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
        if device.subsystem == "usb" and device.device_type == "usb_device":
            await remove_device(qmpsock, device)

async def main():
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    logger.addHandler(handler)

    parser = argparse.ArgumentParser(description="Hot-plugging USB devices to the virtual machines")
    parser.add_argument("--qmp-socket", type=str, required=True, dest="qmpsock", help="Path to the QMP socket")
    parser.add_argument("--add-connected", default=False, action=argparse.BooleanOptionalAction, help="Add already connected devices on startup")
    args = parser.parse_args()

    logger.info(f"Connecting to {args.qmpsock}")
    qemu = QEMULink(args.qmpsock)
    await qemu.wait_for_vm()

    context = pyudev.Context()
    if args.add_connected:
        logger.info("Adding connected devices")
        await add_connected_devices(args.qmpsock, context)

    monitor = pyudev.Monitor.from_netlink(context)

    logger.info("Waiting for a device")
    try:
        while True:
            device = monitor.poll(timeout=1)
            if device != None:
                await device_event(args.qmpsock, device)
    except KeyboardInterrupt:
        logger.info("Ctrl+C")

    logger.info("Exiting")

if __name__ ==  '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

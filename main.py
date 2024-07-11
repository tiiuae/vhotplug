#!/usr/bin/env python

import pyudev
import logging
import asyncio
import sys
from qemulink import *

logger = logging.getLogger("vhotplug")

def log_device(device):
    logger.info(f"{device.device_path}")
    logger.info(f"  sys_path: {device.sys_path}")
    logger.info(f"  sys_name: {device.sys_name}")
    logger.info(f"  sys_number: {device.sys_number}")
    logger.info(f"  tags:")
    for t in device.tags:
        if t:
            logger.info(f"    {t}")
    logger.info(f"  subsystem: {device.subsystem}")
    logger.info(f"  driver: {device.driver}")
    logger.info(f"  device_type: {device.device_type}")
    logger.info(f"  device_node: {device.device_node}")
    logger.info(f"  device_number: {device.device_number}")
    logger.info(f"  is_initialized: {device.is_initialized}")
    logger.info("  Device properties:")
    for i in device.properties:
        logger.info(f"    {i} = {device.properties[i]}")
    logger.info("  Device attributes:")
    for a in device.attributes.available_attributes:
        logger.info(f"    {a}: {device.attributes.get(a)}")
    logger.info("\n")

def find_usb_parent(device):
    return device.find_parent(subsystem='usb', device_type='usb_device')

async def device_event(qmpsock, device):
    qemu = QEMULink(qmpsock)
    if device.action == 'add':
        logger.info(f"Device plugged. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
        if device.subsystem == "input" and device.sys_name.startswith("event"):
            if "ID_INPUT" in device.properties and device.properties["ID_INPUT"] == "1":
                log_device(device)
                usb_dev = find_usb_parent(device)
                if usb_dev != None:
                    logger.info(f"Found parent USB device {usb_dev.sys_name} for {device.sys_name}.")
                    await qemu.add_usb_device(usb_dev)
                else:
                    logger.error(f"Failed to find parent USB device")
    elif device.action == 'remove':
        logger.info(f"Device unplugged. Subsystem: {device.subsystem}. Path: {device.device_path}. Name: {device.sys_name}.")
        if device.subsystem == "usb" and device.device_type == "usb_device":
            await qemu.remove_usb_device(device)

async def main():
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    logger.addHandler(handler)

    if len(sys.argv) < 2:
        logger.error("Usage: main.py <QMP socket>")
        sys.exit(1)

    qmpsock = sys.argv[1]
    logger.info(f"Connecting to {qmpsock}")
    while True:
        qemu = QEMULink(qmpsock)
        try:
            status = await qemu.query_status()
            if status == "running":
                logger.info("The VM is running")
                break
            else:
                logger.info(f"VM status: {status}")
        except Exception as e:
            logger.error(f"Failed to query VM status: {e}")
        await asyncio.sleep(1)

    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)

    logger.info("Waiting for a device")
    try:
        while True:
            device = monitor.poll(timeout=1)
            if device != None:
                await device_event(qmpsock, device)
    except KeyboardInterrupt:
        logger.info("Ctrl+C")

    logger.info("Exiting")

if __name__ ==  '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

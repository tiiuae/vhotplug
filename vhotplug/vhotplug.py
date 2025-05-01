import pyudev
import logging
import asyncio
import argparse
import os
from vhotplug.qemulink import *
from vhotplug.device import *
from vhotplug.config import *
from vhotplug.filewatcher import *
from vhotplug.ghaf_policy import GhafPolicy
from vhotplug.ghaf_dynamic_policy import GhafDynamicPolicy

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
            await attach_usb_device(context, config, device, False)
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

def handle_config(args):
    if not os.path.exists(args.config):
        logger.error(f"Configuration file {args.config} not found")
        raise FileNotFoundError(f"The {args.config} file was not found.") 

    if args.opa and args.config != "":
        logger.error(f"Ghaf Policy and/or OPA can not be enabled with config.")
        raise ValueError("Ghaf Policy and/or OPA can not be enabled with config.") 
        
    config = Config(args.config)
    return config


def handle_policy(args):
    if not os.path.exists(args.policy):
        logger.error(f"Policy file {args.policy} not found")
        raise FileNotFoundError(f"The {args.config} file was not found.") 
        
    policy = GhafPolicy(args.policy, args.evdev_passthrough)

    if args.opa:
        if not args.policy_query or not args.admin_addr:
            parser.error("--policy-query and --admin-addr must be specified when --opa is enabled.")
            raise ValueError("--policy-query and --admin-addr must be specified when --opa is enabled.")
        if args.notls:
            dynamic_policy = GhafDynamicPolicy(
                admin_name = args.admin_name, 
                admin_addr = args.admin_addr, 
                admin_port = args.admin_port, 
                policy_query = args.policy_query, 
                givc_cli = "/run/current-system/sw/bin/givc-cli");
        else:
            dynamic_policy = GhafDynamicPolicy(
                admin_name = args.admin_name, 
                admin_addr = args.admin_addr, 
                admin_port = args.admin_port, 
                policy_query = args.policy_query, 
                givc_cli = "/run/current-system/sw/bin/givc-cli",
                cert = "/etc/givc/cert.pem", 
                key = "/etc/givc/key.pem",
                cacert = "/etc/givc/ca-cert.pem");
        policy.update_policy(dynamic_policy.get_policy())
        
    return policy

async def async_main():
    parser = argparse.ArgumentParser(description="Hot-plugging USB devices to the virtual machines")
    parser.add_argument("-c", "--config", type=str, default="", help="Path to the configuration file")
    parser.add_argument("-p", "--policy", type=str, default="", help="Path to policy file")
    parser.add_argument("-a", "--attach-connected", default=False, action=argparse.BooleanOptionalAction, help="Attach connected devices on startup")
    parser.add_argument("-d", "--debug", default=False, action=argparse.BooleanOptionalAction, help="Enable debug messages")
    parser.add_argument("-e", "--evdev-passthrough", type=str, default="", help="Evdev passthrough configuration, <vmname:pci_bus_prefix>")
    parser.add_argument("--opa", action='store_true', help="Pull OPA policy for USB hotplus")
    parser.add_argument("--notls", action='store_true', help="Dosable TLS for givc communication")
    parser.add_argument("--admin-name", type=str, default="admin-vm", help="Name of Admin vm")
    parser.add_argument("--admin-addr", type=str, default="", help="Address of admin-vm")
    parser.add_argument("--admin-port", type=int, default=9001, help="Port of admin-vm")
    parser.add_argument("--policy-query", type=str, default="", help="Policy query to send to admin-vm")

    args = parser.parse_args()

    vhotplugrules = None
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    if args.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if args.config != "":
        vhotplugrules = handle_config(args)
    else:
        vhotplugrules = handle_policy(args)
        
    context = pyudev.Context()
    if args.attach_connected:
        await attach_connected_devices(context, vhotplugrules)

    monitor = pyudev.Monitor.from_netlink(context)

    watcher = FileWatcher()
    for vm in vhotplugrules.get_all_vms():
        qmp_socket = vm.get("qmpSocket")
        watcher.add_file(qmp_socket)

    logger.info("Waiting for new devices")
    try:
        while True:
            device = monitor.poll(timeout=1)
            if device != None:
                await device_event(context, vhotplugrules, device)
            if watcher.detect_restart() == True and args.attach_connected:
                await attach_connected_devices(context, vhotplugrules)

    except KeyboardInterrupt:
        logger.info("Ctrl+C")

    logger.info("Exiting")

def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(async_main())

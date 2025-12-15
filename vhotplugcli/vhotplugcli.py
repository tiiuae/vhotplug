import argparse
import fcntl
import logging
import socket
import struct
import time

from vhotplugcli.apiclient import APIClient

logger = logging.getLogger(__name__)


# pylint: disable=too-many-positional-arguments
def usb_attach(
    client: APIClient,
    devnode: str | None,
    bus: int | None,
    port: int | None,
    vid: str | None,
    pid: str | None,
    vm: str,
) -> None:
    if not devnode and not (bus and port) and not (vid and pid):
        raise RuntimeError("You must specify either --devnode or --bus and --port or --vid and --pid")

    client.connect()
    if devnode:
        res = client.usb_attach(devnode, vm)
    elif bus and port:
        res = client.usb_attach_by_bus_port(bus, port, vm)
    else:
        assert vid is not None and pid is not None, "vid and pid must be provided"
        res = client.usb_attach_by_vid_pid(vid, pid, vm)

    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to attach USB: {res.get('error')}")
    logger.info("Successfully attached")


def usb_detach(
    client: APIClient,
    devnode: str | None,
    bus: int | None,
    port: int | None,
    vid: str | None,
    pid: str | None,
) -> None:
    if not devnode and not (bus and port) and not (vid and pid):
        raise RuntimeError("You must specify either --devnode or --bus and --port or --vid and --pid")

    client.connect()
    if devnode:
        res = client.usb_detach(devnode)
    elif bus and port:
        res = client.usb_detach_by_bus_port(bus, port)
    else:
        assert vid is not None and pid is not None, "vid and pid must be provided"
        res = client.usb_detach_by_vid_pid(vid, pid)

    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to detach USB: {res.get('error')}")
    logger.info("Successfully detached")


def usb_list(client: APIClient, disconnected: bool, short: bool) -> None:
    client.connect()
    res = client.usb_list(disconnected)
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to get USB list: {res.get('error')}")
    logger.debug("USB list: %s", res)
    for dev in res.get("usb_devices", []):
        print(f"{dev['vid']}:{dev['pid']} {dev['vendor_name']} {dev['product_name']}")
        if not short:
            for key, value in dev.items():
                print(f"  {key:<16}: {value}")
            print()


def usb_suspend(client: APIClient, vm: str) -> None:
    client.connect()
    res = client.usb_suspend(vm)
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to suspend USB: {res.get('error')}")
    logger.info("Successfully suspended")


def usb_resume(client: APIClient, vm: str) -> None:
    client.connect()
    res = client.usb_resume(vm)
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to resume USB: {res.get('error')}")
    logger.info("Successfully resumed")


def listen_for_notifications(client: APIClient) -> None:
    logger.info("Listening for notifications")
    client.recv_notifications(callback=logger.info)


def pci_attach(client: APIClient, address: str | None, vid: str | None, did: str | None, vm: str) -> None:
    if not address and not (vid and did):
        raise RuntimeError("You must specify either --address or --vid and --did")

    client.connect()
    if address:
        res = client.pci_attach(address, vm)
    else:
        assert vid is not None and did is not None, "vid and did must be provided"
        res = client.pci_attach_by_vid_did(vid, did, vm)

    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to attach PCI: {res.get('error')}")
    logger.info("Successfully attached")


def pci_detach(client: APIClient, address: str | None, vid: str | None, did: str | None) -> None:
    if not address and not (vid and did):
        raise RuntimeError("You must specify either --address or --vid and --did")

    client.connect()
    if address:
        res = client.pci_detach(address)
    else:
        assert vid is not None and did is not None, "vid and did must be provided"
        res = client.pci_detach_by_vid_did(vid, did)

    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to detach PCI: {res.get('error')}")
    logger.info("Successfully detached")


def pci_list(client: APIClient, disconnected: bool, short: bool) -> None:
    client.connect()
    res = client.pci_list(disconnected)
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to get PCI list: {res.get('error')}")
    logger.debug("PCI list: %s", res)
    for dev in res.get("pci_devices", []):
        print(f"{dev['address']} {dev['vid']}:{dev['did']} {dev['vendor_name']} {dev['device_name']}")
        if not short:
            for key, value in dev.items():
                print(f"  {key:<16}: {value}")
            print()


def pci_suspend(client: APIClient, vm: str) -> None:
    client.connect()
    res = client.pci_suspend(vm)
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to suspend PCI: {res.get('error')}")
    logger.info("Successfully suspended")


def pci_resume(client: APIClient, vm: str) -> None:
    client.connect()
    res = client.pci_resume(vm)
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to resume PCI: {res.get('error')}")
    logger.info("Successfully resumed")


def pci_vmm_args(client: APIClient, vm: str, qemu_bus_prefix: str | None) -> None:
    while True:
        try:
            client.connect()
            res = client.pci_vmm_args(vm, qemu_bus_prefix)
        except RuntimeError as e:
            logger.warning(str(e))
            time.sleep(1)
            continue

        if res.get("result") == "failed":
            raise RuntimeError(f"Failed to get VMM args for PCI devices: {res.get('error')}")
        args = res.get("vmm_args", [])
        cmdline = " ".join(args)
        print(cmdline, end="")
        break


def running_in_vm() -> bool:
    try:
        with open("/dev/vsock", "rb") as fd:
            buf = bytearray(4)
            fcntl.ioctl(fd, socket.IOCTL_VM_SOCKETS_GET_LOCAL_CID, buf)
            cid = struct.unpack("I", buf)[0]
            logger.debug("Local CID: %d", cid)
            # 1 = VMADDR_CID_LOCAL
            return cid not in (socket.VMADDR_CID_ANY, socket.VMADDR_CID_HOST, 1)
    except OSError:
        return False


# pylint: disable=too-many-locals,too-many-statements
def main() -> int:
    parser = argparse.ArgumentParser(prog="vhotplugcli", description="CLI tool for managing virtual hotplug devices")

    parser.add_argument(
        "-d",
        "--debug",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Enable debug messages",
    )
    parser.add_argument(
        "-t",
        "--transport",
        choices=["unix", "tcp", "vsock"],
        help="Transport type (default: vsock when running in a VM, otherwise unix)",
    )
    parser.add_argument(
        "-u",
        "--path",
        default="/var/lib/vhotplug/vhotplug.sock",
        help="Path to Unix socket (default: /var/lib/vhotplug/vhotplug.sock)",
    )
    parser.add_argument("-s", "--host", default="127.0.0.1", help="TCP host (default: 127.0.0.1)")
    parser.add_argument(
        "-p",
        "--net-port",
        type=int,
        default=2000,
        help="TCP or VSOCK port (default: 2000)",
    )
    parser.add_argument(
        "-c",
        "--cid",
        type=int,
        default=socket.VMADDR_CID_HOST,
        help="VSOCK CID (default: VMADDR_CID_HOST = 2)",
    )

    subparsers = parser.add_subparsers(dest="subsystem", required=True)

    usb_parser = subparsers.add_parser("usb", help="Manage USB devices")
    usb_sub = usb_parser.add_subparsers(dest="action", required=True)

    usb_attach_parser = usb_sub.add_parser("attach", help="Attach USB device")
    usb_attach_parser.add_argument("--devnode", help="Path to USB device node (/dev/bus/usb/...)")
    usb_attach_parser.add_argument("--bus", type=int, help="USB bus")
    usb_attach_parser.add_argument("--port", type=int, help="USB port")
    usb_attach_parser.add_argument("--vid", help="USB Vendor ID")
    usb_attach_parser.add_argument("--pid", help="USB Product ID")
    usb_attach_parser.add_argument("--vm", help="Virtual machine name")
    usb_attach_parser.set_defaults(func=lambda a, c: usb_attach(c, a.devnode, a.bus, a.port, a.vid, a.pid, a.vm))

    usb_detach_parser = usb_sub.add_parser("detach", help="Detach USB device")
    usb_detach_parser.add_argument("--devnode", help="Path to USB device node (/dev/bus/usb/...)")
    usb_detach_parser.add_argument("--bus", type=int, help="USB bus")
    usb_detach_parser.add_argument("--port", type=int, help="USB port")
    usb_detach_parser.add_argument("--vid", help="USB Vendor ID")
    usb_detach_parser.add_argument("--pid", help="USB Product ID")
    usb_detach_parser.set_defaults(func=lambda a, c: usb_detach(c, a.devnode, a.bus, a.port, a.vid, a.pid))

    usb_list_parser = usb_sub.add_parser("list", help="Get USB list")
    usb_list_parser.add_argument("--disconnected", help="Show only disconnected devices", action="store_true")
    usb_list_parser.add_argument("--short", help="Show device names only, without details", action="store_true")
    usb_list_parser.set_defaults(func=lambda a, c: usb_list(c, a.disconnected, a.short))

    usb_suspend_parser = usb_sub.add_parser("suspend", help="USB suspend")
    usb_suspend_parser.add_argument("--vm", help="Virtual machine name")
    usb_suspend_parser.set_defaults(func=lambda a, c: usb_suspend(c, a.vm))

    usb_resume_parser = usb_sub.add_parser("resume", help="USB resume")
    usb_resume_parser.add_argument("--vm", help="Virtual machine name")
    usb_resume_parser.set_defaults(func=lambda a, c: usb_resume(c, a.vm))

    listen_parser = subparsers.add_parser("listen", help="Listen for notifications")
    listen_parser.set_defaults(func=lambda a, c: listen_for_notifications(c))

    pci_parser = subparsers.add_parser("pci", help="Manage PCI devices")
    pci_sub = pci_parser.add_subparsers(dest="action", required=True)

    pci_attach_parser = pci_sub.add_parser("attach", help="Attach PCI device")
    pci_attach_parser.add_argument("--address", help="PCI Address (e.g., 0000:00:01.0)")
    pci_attach_parser.add_argument("--vid", help="USB Vendor ID")
    pci_attach_parser.add_argument("--did", help="USB Device ID")
    pci_attach_parser.add_argument("--vm", help="Virtual machine name")
    pci_attach_parser.set_defaults(func=lambda a, c: pci_attach(c, a.address, a.vid, a.did, a.vm))

    pci_detach_parser = pci_sub.add_parser("detach", help="Detach PCI device")
    pci_detach_parser.add_argument("--address", help="PCI Address (e.g., 0000:00:01.0)")
    pci_detach_parser.add_argument("--vid", help="PCI Vendor ID")
    pci_detach_parser.add_argument("--did", help="PCI Device ID")
    pci_detach_parser.set_defaults(func=lambda a, c: pci_detach(c, a.address, a.vid, a.did))

    pci_list_parser = pci_sub.add_parser("list", help="Get PCI list")
    pci_list_parser.add_argument("--disconnected", help="Show only disconnected devices", action="store_true")
    pci_list_parser.add_argument("--short", help="Show device names only, without details", action="store_true")
    pci_list_parser.set_defaults(func=lambda a, c: pci_list(c, a.disconnected, a.short))

    pci_suspend_parser = pci_sub.add_parser("suspend", help="PCI suspend")
    pci_suspend_parser.add_argument("--vm", help="Virtual machine name")
    pci_suspend_parser.set_defaults(func=lambda a, c: pci_suspend(c, a.vm))

    pci_resume_parser = pci_sub.add_parser("resume", help="PCI resume")
    pci_resume_parser.add_argument("--vm", help="Virtual machine name")
    pci_resume_parser.set_defaults(func=lambda a, c: pci_resume(c, a.vm))

    pci_vmm_args_parser = pci_sub.add_parser("vmmargs", help="Get VMM arguments for PCI devices")
    pci_vmm_args_parser.add_argument("--vm", help="Virtual machine name")
    pci_vmm_args_parser.add_argument("--qemu-bus-prefix", help="QEMU Bus Prefix")
    pci_vmm_args_parser.set_defaults(func=lambda a, c: pci_vmm_args(c, a.vm, a.qemu_bus_prefix))

    args = parser.parse_args()

    # Setup logging
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG if args.debug else logging.INFO)

    try:
        transport = args.transport or ("vsock" if running_in_vm() else "unix")
        client = APIClient(
            transport=transport,
            path=args.path,
            host=args.host,
            port=args.net_port,
            cid=args.cid,
        )
        args.func(args, client)
    except (ValueError, OSError, RuntimeError) as e:
        logger.error(str(e))  # noqa: TRY400
        return 1
    except KeyboardInterrupt:
        logger.info("Exiting")
    return 0

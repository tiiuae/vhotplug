import logging
import argparse
import socket
import struct
import fcntl
from vhotplugcli.apiclient import APIClient

logger = logging.getLogger(__name__)

# pylint: disable=too-many-positional-arguments
def usb_attach(client: APIClient, devnode, bus, port, vid, pid, vm):
    if not devnode and not (bus and port) and not (vid and pid):
        raise RuntimeError("You must specify either --devnode or --bus and --port or --vid and --pid")

    client.connect()
    if devnode:
        res = client.usb_attach(devnode, vm)
    elif bus and port:
        res = client.usb_attach_by_bus_port(bus, port, vm)
    else:
        res = client.usb_attach_by_vid_pid(vid, pid, vm)

    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to attach USB: {res.get('error')}")
    logger.info("Successfully attached")

def usb_detach(client: APIClient, devnode, bus, port, vid, pid):
    if not devnode and not (bus and port) and not (vid and pid):
        raise RuntimeError("You must specify either --devnode or --bus and --port or --vid and --pid")

    client.connect()
    if devnode:
        res = client.usb_detach(devnode)
    elif bus and port:
        res = client.usb_detach_by_bus_port(bus, port)
    else:
        res = client.usb_detach_by_vid_pid(vid, pid)

    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to detach USB: {res.get('error')}")
    logger.info("Successfully detached")

def usb_list(client: APIClient):
    client.connect()
    res = client.usb_list()
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to get USB list: {res.get('error')}")
    logger.debug("USB list: %s", res)
    for dev in res.get("usb_devices", []):
        print(f"USB Device: {dev['vid']}:{dev['pid']} {dev['vendor_name']} {dev['product_name']}")
        for key, value in dev.items():
            print(f"  {key:<16}: {value}")
        print()

def usb_suspend(client: APIClient):
    client.connect()
    res = client.usb_suspend()
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to suspend USB: {res.get('error')}")
    logger.info("Successfully suspended")

def usb_resume(client: APIClient):
    client.connect()
    res = client.usb_resume()
    if res.get("result") == "failed":
        raise RuntimeError(f"Failed to resume USB: {res.get('error')}")
    logger.info("Successfully resumed")

def listen_for_notifications(client: APIClient):
    logger.info("Listening for notifications")
    client.recv_notifications(callback=logger.info)

def running_in_vm():
    try:
        with open("/dev/vsock", "rb") as fd:
            buf = bytearray(4)
            fcntl.ioctl(fd, socket.IOCTL_VM_SOCKETS_GET_LOCAL_CID, buf)
            cid = struct.unpack("I", buf)[0]
            logger.debug("Local CID: %d", cid)
            return cid not in (socket.VMADDR_CID_ANY, socket.VMADDR_CID_HOST)
    except OSError:
        return False

def main():
    parser = argparse.ArgumentParser(prog="vhotplugcli", description="CLI tool for managing virtual hotplug devices")

    parser.add_argument("-d", "--debug", default=False, action=argparse.BooleanOptionalAction, help="Enable debug messages",)
    parser.add_argument("-t", "--transport", choices=["unix", "tcp", "vsock"], help="Transport type (default: vsock when running in a VM, otherwise unix)")
    parser.add_argument("-u", "--path", default="/var/lib/vhotplug/vhotplug.sock", help="Path to Unix socket (default: /var/lib/vhotplug/vhotplug.sock)")
    parser.add_argument("-s", "--host", default="127.0.0.1", help="TCP host (default: 127.0.0.1)")
    parser.add_argument("-p", "--net-port", type=int, default=2000, help="TCP or VSOCK port (default: 2000)")
    parser.add_argument("-c", "--cid", type=int, default=socket.VMADDR_CID_HOST, help="VSOCK CID (default: VMADDR_CID_HOST = 2)")

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
    usb_list_parser.set_defaults(func=lambda a, c: usb_list(c))

    usb_suspend_parser = usb_sub.add_parser("suspend", help="USB suspend")
    usb_suspend_parser.set_defaults(func=lambda a, c: usb_suspend(c))

    usb_resume_parser = usb_sub.add_parser("resume", help="USB resume")
    usb_resume_parser.set_defaults(func=lambda a, c: usb_resume(c))

    listen_parser = subparsers.add_parser("listen", help="Listen for notifications")
    listen_parser.set_defaults(func=lambda a, c: listen_for_notifications(c))

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
        client = APIClient(transport=transport, path=args.path, host=args.host, port=args.net_port, cid=args.cid)
        args.func(args, client)
    except (RuntimeError, ValueError, OSError) as e:
        logger.error(str(e))
        return 1
    except KeyboardInterrupt:
        logger.info("Exiting")
    return 0

import logging
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

import psutil
import pyudev

from vhotplug.appcontext import AppContext

logger = logging.getLogger("vhotplug")


class USBInfo(NamedTuple):
    device_node: str | None = None

    vid: str | None = None
    pid: str | None = None
    vendor_name: str | None = None
    product_name: str | None = None
    interfaces: str | None = None
    device_class: int | None = None
    device_subclass: int | None = None
    device_protocol: int | None = None
    busnum: int | None = None
    devnum: int | None = None
    serial: str | None = None
    ports: list[int] | None = None
    sys_name: str | None = None
    bcd_device: int | None = None
    sys_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_node": self.device_node,
            "vid": self.vid,
            "pid": self.pid,
            "vendor_name": self.vendor_name,
            "product_name": self.product_name,
            "interfaces": self.interfaces,
            "device_class": self.device_class,
            "device_subclass": self.device_subclass,
            "device_protocol": self.device_protocol,
            "busnum": self.busnum,
            "devnum": self.devnum,
            "portnum": self.root_port,
            "serial": self.serial,
            "sys_name": self.sys_name,
        }

    def friendly_name(self) -> str | None:
        if self.vid and self.pid:
            return f"{self.vid}:{self.pid} ({self.vendor_name} {self.product_name})"
        return self.device_node

    def runtime_id(self) -> str:
        return f"usb-{self.device_node}"

    def persistent_id(self) -> str:
        return f"usb-{self.vid}:{self.pid}:{self.serial}"

    @property
    def root_port(self) -> int | None:
        return self.ports[0] if self.ports else None

    def is_boot_device(self, context: pyudev.Context) -> bool:
        # Find device partitions
        for udevpart in context.list_devices(subsystem="block", DEVTYPE="partition"):
            parent = udevpart.find_parent("usb", "usb_device")
            if parent and parent.device_node == self.device_node:
                logger.debug(
                    "USB drive %s has partition %s",
                    self.device_node,
                    udevpart.device_node,
                )
                # Find mountpoints
                partitions = psutil.disk_partitions(all=True)
                for part in partitions:
                    if part.device == udevpart.device_node:
                        logger.debug(
                            "Found mountpoint %s with filesystem %s",
                            part.mountpoint,
                            part.fstype,
                        )
                        logger.debug("Options: %s", part.opts)
                        if part.mountpoint == "/boot":
                            return True
        return False

    def get_interfaces(self) -> list[dict[str, int]]:
        result: list[dict[str, int]] = []
        if self.interfaces:
            try:
                for interface in self.interfaces.strip(":").split(":"):
                    if len(interface) >= 6:
                        usb_class = interface[:2]
                        usb_subclass = interface[2:4]
                        usb_protocol = interface[4:6]
                        result.append(
                            {
                                "class": int(usb_class, 16),
                                "subclass": int(usb_subclass, 16),
                                "protocol": int(usb_protocol, 16),
                            }
                        )
            except (ValueError, TypeError):
                logger.exception("Failed to parse USB interfaces")
        return result

    def is_usb_hub(self) -> bool:
        if self.device_class == 9:
            return True

        usb_interfaces = self.get_interfaces()
        for interface in usb_interfaces:
            interface_class = interface["class"]
            if interface_class == 9:
                return True
        return False

    def _modalias(self, iface_class: int, iface_subclass: int, iface_protocol: int, iface_number: int) -> str | None:
        if self.vid and self.pid:
            return (
                f"usb:v{self.vid.upper()}p{self.pid.upper()}"
                f"d{self.bcd_device:04X}"
                f"dc{self.device_class:02X}"
                f"dsc{self.device_subclass:02X}"
                f"dp{self.device_protocol:02X}"
                f"ic{iface_class:02X}"
                f"isc{iface_subclass:02X}"
                f"ip{iface_protocol:02X}"
                f"in{iface_number:02X}"
            )
        return None

    def get_modaliases(self) -> list[str]:
        result: list[str] = []
        iface_number = 0
        for interface in self.get_interfaces():
            interface_class = interface["class"]
            interface_subclass = interface["subclass"]
            interface_protocol = interface["protocol"]
            modalias = self._modalias(interface_class, interface_subclass, interface_protocol, iface_number)
            if modalias:
                result.append(modalias)
            iface_number = iface_number + 1
        return result

    def exists(self) -> bool:
        if not self.sys_path:
            logger.warning("USB device %s does not have sys_path", self.friendly_name())
            return False
        return Path(self.sys_path).exists()


def _bytes_to_int(data: bytes | None) -> int | None:
    if not data:
        return None
    try:
        return int(data.decode().strip(), 16)
    except ValueError:
        return None


def _get_ports(sys_name: str) -> list[int]:
    try:
        parts = sys_name.split("-")
        # bus = int(parts[0])
        ports = [int(x) for x in parts[1].split(".")]
    except (IndexError, ValueError):
        ports = []
    return ports


def get_usb_info(device: pyudev.Device) -> USBInfo:
    device_node = device.device_node
    vid = device.properties.get("ID_VENDOR_ID")
    pid = device.properties.get("ID_MODEL_ID")
    vendor_name = device.properties.get("ID_VENDOR_FROM_DATABASE") or device.properties.get("ID_VENDOR")
    product_name = device.properties.get("ID_MODEL_FROM_DATABASE") or device.properties.get("ID_MODEL")
    interfaces = device.properties.get("ID_USB_INTERFACES")
    device_class = _bytes_to_int(device.attributes.get("bDeviceClass"))
    device_subclass = _bytes_to_int(device.attributes.get("bDeviceSubClass"))
    device_protocol = _bytes_to_int(device.attributes.get("bDeviceProtocol"))
    busnum = int(device.properties.get("BUSNUM"))
    devnum = int(device.properties.get("DEVNUM"))
    serial = device.properties.get("ID_SERIAL_SHORT")
    ports = _get_ports(device.sys_name)
    sys_name = device.sys_name
    bcd_device = _bytes_to_int(device.attributes.get("bcdDevice"))
    sys_path = device.sys_path

    return USBInfo(
        device_node,
        vid,
        pid,
        vendor_name,
        product_name,
        interfaces,
        device_class,
        device_subclass,
        device_protocol,
        busnum,
        devnum,
        serial,
        ports,
        sys_name,
        bcd_device,
        sys_path,
    )


def is_usb_device(device: pyudev.Device) -> bool:
    return bool(device.subsystem == "usb" and device.device_type == "usb_device")


def find_usb_parent(device: pyudev.Device) -> pyudev.Device | None:
    return device.find_parent(subsystem="usb", device_type="usb_device")


def usb_device_by_node(app_context: AppContext, device_node: str) -> pyudev.Device | None:
    try:
        return pyudev.Devices.from_device_file(app_context.udev_context, device_node)
    except pyudev.DeviceNotFoundError:
        return None


def usb_device_by_bus_port(app_context: AppContext, bus: int, port: int) -> pyudev.Device | None:
    for device in app_context.udev_context.list_devices(subsystem="usb"):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            if usb_info.busnum == bus and usb_info.root_port == port:
                return device
    return None


def usb_device_by_vid_pid(app_context: AppContext, vid: str, pid: str) -> pyudev.Device | None:
    for device in app_context.udev_context.list_devices(subsystem="usb"):
        if is_usb_device(device):
            usb_info = get_usb_info(device)
            if (
                usb_info.vid
                and usb_info.pid
                and usb_info.vid.casefold() == vid.casefold()
                and usb_info.pid.casefold() == pid.casefold()
            ):
                return device
    return None


def _get_drivers_from_modalias(modalias: str, modprobe: str, modinfo: str) -> list[str] | None:
    try:
        # Resolve alias
        result = subprocess.run(
            [modprobe, "-R", modalias],
            capture_output=True,
            text=True,
            check=True,
        )
        # A single modalias can have multiple drivers
        output = result.stdout.strip()
        if output:
            modules = output.splitlines()
    except subprocess.CalledProcessError as e:
        # Some devices don't have an entry in modules.alias
        logger.debug("Failed to resolve modalias %s", modalias)
        logger.debug("Error: %s", e.stderr)
        return None
    except OSError as e:
        logger.warning("Failed to resolve modalias %s: %s", modalias, str(e))
        return None

    drivers: list[str] = []
    for module_name in modules:
        logger.debug("Modalias %s module name: %s", modalias, module_name)

        try:
            # Get module path
            result = subprocess.run(
                [modinfo, "-n", module_name],
                capture_output=True,
                text=True,
                check=True,
            )
            driver = result.stdout.strip()
            logger.debug("Modalias %s driver: %s", modalias, driver)
            drivers.append(driver)

        except subprocess.CalledProcessError as e:
            logger.warning("Failed to get driver path for %s", modalias)
            logger.warning("Error: %s", e.stderr)
            return None
        except OSError as e:
            logger.warning("Failed to get driver path for %s: %s", modalias, str(e))
            return None

    return drivers


def get_drivers_from_modaliases(modaliases: list[str], modprobe: str, modinfo: str) -> list[str]:
    result: list[str] = []
    for modalias in modaliases:
        drivers = _get_drivers_from_modalias(modalias, modprobe, modinfo)
        for d in drivers or []:
            if d not in result:
                result.append(d)
    return result


def get_usb_authorized_default() -> int | None:
    """Get the default USB authorization state from usbcore."""
    usbcore = Path("/sys/module/usbcore/parameters/authorized_default")
    if not usbcore.exists():
        return None

    try:
        return int(usbcore.read_text().strip())
    except (OSError, ValueError) as e:
        logger.warning("Failed to get default USB authorization for usbcore: %s", e)
        return None


def set_usb_authorized_default(authorized: int) -> None:
    """Set the default authorization state for usbcore and all USB root hubs."""
    # Set global usbcore authorized_default
    usbcore = Path("/sys/module/usbcore/parameters/authorized_default")
    if usbcore.exists():
        try:
            usbcore.write_text(str(authorized))
        except OSError as e:
            logger.warning("Failed to set default USB authorization for usbcore: %s", e)

    # Set authorized_default for all USB root hubs
    for path in Path("/sys/bus/usb/devices").glob("usb*/authorized_default"):
        try:
            path.write_text(str(authorized))
        except OSError as e:
            logger.warning("Failed to set default USB authorization for %s: %s", path, e)


def authorize_usb_hubs(context: pyudev.Context) -> None:
    """Authorize all connected USB hubs so devices behind them can be enumerated."""
    logger.info("Authorizing all connected USB hubs")
    for device in context.list_devices(subsystem="usb"):
        if not is_usb_device(device):
            continue

        usb_info = get_usb_info(device)
        if not usb_info.is_usb_hub() or get_usb_device_authorization(usb_info) == 1:
            continue

        logger.info("Authorizing USB hub %s", usb_info.friendly_name())
        authorize_usb_device(usb_info)


def get_usb_device_authorization(usb_info: USBInfo) -> int | None:
    if not usb_info.sys_path:
        return None

    try:
        path = Path(usb_info.sys_path) / "authorized"
        if not path.is_file():
            return None

        return int(path.read_text().strip())
    except (OSError, ValueError) as e:
        logger.warning("Failed to read USB authorization for %s: %s", usb_info.friendly_name(), e)
        return None


def authorize_usb_device(usb_info: USBInfo, authorized: bool = True) -> bool:
    """Authorize a USB device."""
    if not usb_info.sys_path:
        logger.warning("USB device %s does not have sys_path", usb_info.friendly_name())
        return False

    if not usb_info.exists():
        logger.warning("USB device %s not found", usb_info.friendly_name())
        return False

    path = Path(usb_info.sys_path) / "authorized"
    if not path.is_file():
        logger.warning("USB device %s does not have authorized field", usb_info.friendly_name())
        return False

    try:
        path.write_text("1" if authorized else "0")
        return True
    except OSError as e:
        logger.warning("Failed to set authorized for %s: %s", usb_info.friendly_name(), e)
        return False


def deauthorize_usb_device(usb_info: USBInfo) -> bool:
    """Deauthorize a USB device."""
    return authorize_usb_device(usb_info, False)

import logging
from typing import Any

from vhotplug.appcontext import AppContext
from vhotplug.crosvmlink import CrosvmLink
from vhotplug.evdev import EvdevInfo
from vhotplug.pci import PCIInfo
from vhotplug.qemulink import QEMULink
from vhotplug.usb import USBInfo

logger = logging.getLogger("vhotplug")


def _get_crosvm_bin(app_context: AppContext) -> str | None:
    """Returns a path to crosvm binary from config or None."""
    crosvm_bin = app_context.config.config.get("general", {}).get("crosvm")
    return crosvm_bin if isinstance(crosvm_bin, str) else None


async def vmm_add_device(app_context: AppContext, vm: dict[str, str], dev_info: USBInfo | PCIInfo | EvdevInfo) -> None:
    """Attaches a device to the VM based on the VMM type and device type."""
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    if not vm_socket:
        raise RuntimeError("No socket path defined")

    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        if isinstance(dev_info, USBInfo):
            # await qemu.add_usb_device_by_vid_pid(usb_info)
            await qemu.add_usb_device(dev_info)
        elif isinstance(dev_info, PCIInfo):
            await qemu.add_pci_device(dev_info)
        else:
            await qemu.add_evdev_device(dev_info)
    elif vm_type == "crosvm":
        crosvm = CrosvmLink(vm_socket, _get_crosvm_bin(app_context))
        if isinstance(dev_info, USBInfo):
            await crosvm.add_usb_device(dev_info)
        elif isinstance(dev_info, PCIInfo):
            await crosvm.add_pci_device(dev_info)
        else:
            raise RuntimeError(f"Evdev passthrough is not supported by {vm_type}")
    else:
        raise RuntimeError(f"Unknown VM type: {vm_type}")


async def vmm_remove_device(app_context: AppContext, vm: dict[str, Any], dev_info: USBInfo | PCIInfo) -> None:
    """Removes a device from the VM based on the VMM type and device type."""
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    if not vm_socket:
        raise RuntimeError("No socket path defined")

    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        if isinstance(dev_info, USBInfo):
            await qemu.remove_usb_device(dev_info)
        else:
            # Here we can detach the device by its QEMU ID if vhotplug manages all devices.
            # This would be the most efficient and simple method but it won't work if the device was attached by something else since we don't know the QEMU ID.
            # await qemu.remove_pci_device(dev_info)
            # This method searches for a device by enumerating guest PCI devices and comparing the vendor ID and device ID.
            # It is less efficient but allows support for devices that were added via QEMU command line or by another manager.
            await qemu.remove_pci_device_by_vid_did(dev_info)

    elif vm_type == "crosvm":
        # Crosvm seems to automatically remove the device from the list so this code is not really used
        crosvm = CrosvmLink(vm_socket, _get_crosvm_bin(app_context))
        if isinstance(dev_info, USBInfo):
            await crosvm.remove_usb_device(dev_info)
        else:
            await crosvm.remove_pci_device(dev_info)
    else:
        raise RuntimeError(f"Unsupported vm type: {vm_type}")


async def vmm_pause(vm: dict[str, str]) -> None:
    """Pauses VM execution."""
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    if not vm_socket:
        raise RuntimeError("No socket path defined")

    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        await qemu.pause()


async def vmm_resume(vm: dict[str, str]) -> None:
    """Resumes VM execution."""
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    if not vm_socket:
        raise RuntimeError("No socket path defined")

    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        await qemu.resume()


async def vmm_is_pci_dev_connected(vm: dict[str, str], pci_info: PCIInfo) -> bool:
    vm_type = vm.get("type")
    vm_socket = vm.get("socket")
    if not vm_socket:
        raise RuntimeError("No socket path defined")

    if vm_type == "qemu":
        qemu = QEMULink(vm_socket)
        return await qemu.is_pci_dev_connected(pci_info)
    return False


def vmm_args_pci(vm: dict[str, str], dev: PCIInfo, n: int, qemu_bus_prefix: str | None) -> list[str]:
    vm_type = vm.get("type")
    sys_name = dev.address
    if vm_type == "qemu":
        qemuid = f"vhp-pci-{n}"
        bus = f",bus={qemu_bus_prefix}{n}" if qemu_bus_prefix else ""
        return ["-device", f"vfio-pci,host={sys_name},multifunction=on,id={qemuid}{bus}"]
    if vm_type == "crosvm":
        return ["--vfio", f"/sys/bus/pci/devices/{sys_name},iommu=viommu"]
    if vm_type == "cloud-hypervisor":
        return ["--device", f"path=/sys/bus/pci/devices/{sys_name}"]
    raise RuntimeError(f"Unsupported vm type: {vm_type}")


def vmm_args_evdev(vm: dict[str, str], dev: dict[str, str]) -> list[str]:
    vm_type = vm.get("type")
    device_node = dev["device_node"]
    if vm_type == "qemu":
        return ["-device", f"'virtio-input-host-pci,evdev={device_node}'"]
    if vm_type == "crosvm":
        return ["--input", f"evdev[path={device_node}]"]
    if vm_type == "cloud-hypervisor":
        raise RuntimeError("Cloud Hypervisor doesn't support evdev passthrough")
    raise RuntimeError(f"Unsupported vm type: {vm_type}")

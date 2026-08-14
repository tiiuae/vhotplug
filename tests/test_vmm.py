import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from vhotplug.pci import PCIInfo, pci_is_nvidia_gpu
from vhotplug.usb import USBInfo
from vhotplug.vmm import vmm_add_device, vmm_args_ovmf, vmm_args_pci


def test_pci_is_nvidia_gpu_display_controller() -> None:
    dev = PCIInfo(address="0000:01:00.0", vendor_id=0x10DE, pci_class=0x03)
    assert pci_is_nvidia_gpu(dev)


def test_pci_is_nvidia_gpu_audio_function() -> None:
    dev = PCIInfo(address="0000:01:00.1", vendor_id=0x10DE, pci_class=0x04)
    assert not pci_is_nvidia_gpu(dev)


def test_vmm_args_ovmf() -> None:
    vm = {
        "type": "qemu",
    }
    assert vmm_args_ovmf(vm, "/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd") == [
        "-drive",
        "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd",
        "-drive",
        "if=pflash,format=raw,unit=1,readonly=on,file=/usr/share/OVMF/OVMF_VARS.fd",
    ]


def test_vmm_args_ovmf_requires_paths() -> None:
    with pytest.raises(RuntimeError, match="ovmfCode"):
        vmm_args_ovmf({"type": "qemu"}, None, None)


def test_vmm_args_pci_uses_bus_prefix() -> None:
    vm = {"type": "qemu"}
    dev = PCIInfo(address="0000:01:00.0")
    assert vmm_args_pci(vm, dev, 3, "pci.") == [
        "-device",
        "vfio-pci,host=0000:01:00.0,multifunction=on,id=vhp-pci-3,bus=pci.3",
    ]


def test_vmm_args_pci_uses_default_bus() -> None:
    vm = {"type": "qemu"}
    dev = PCIInfo(address="0000:01:00.0")
    assert vmm_args_pci(vm, dev, 3, "pci.", True) == [
        "-device",
        "vfio-pci,host=0000:01:00.0,multifunction=on,id=vhp-pci-3",
    ]


def test_vmm_add_device_returns_crosvm_usb_port(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCrosvm:
        def __init__(self, *_args: Any) -> None:
            pass

        async def add_usb_device(self, _usb_info: USBInfo, known_port: int | None) -> int:
            assert known_port == 3
            return 7

    monkeypatch.setattr("vhotplug.vmm.CrosvmLink", FakeCrosvm)
    app_context: Any = SimpleNamespace(config=SimpleNamespace(config={}))

    port = asyncio.run(
        vmm_add_device(
            app_context,
            {"name": "app-vm", "type": "crosvm", "socket": "/run/app-vm.sock"},
            USBInfo(device_node="/dev/bus/usb/001/002"),
            3,
        )
    )

    assert port == 7


def test_vmm_add_device_returns_none_for_qemu_usb(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeQemu:
        def __init__(self, *_args: Any) -> None:
            pass

        async def add_usb_device(self, _usb_info: USBInfo) -> None:
            pass

    monkeypatch.setattr("vhotplug.vmm.QEMULink", FakeQemu)
    app_context: Any = SimpleNamespace(config=SimpleNamespace(config={}))

    port = asyncio.run(
        vmm_add_device(
            app_context,
            {"name": "app-vm", "type": "qemu", "socket": "/run/app-vm.sock"},
            USBInfo(device_node="/dev/bus/usb/001/002"),
        )
    )

    assert port is None

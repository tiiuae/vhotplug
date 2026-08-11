import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from vhotplug import vmm as vmm_module
from vhotplug.appcontext import AppContext
from vhotplug.pci import PCIInfo, pci_is_nvidia_gpu
from vhotplug.usb import USBInfo
from vhotplug.vmm import (
    vmm_add_device,
    vmm_args_acpi_table,
    vmm_args_ovmf,
    vmm_args_pci,
    vmm_is_pci_dev_connected,
    vmm_wait_until_removed,
)


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


def test_vmm_args_pci_crosvm_is_removable() -> None:
    assert vmm_args_pci({"type": "crosvm"}, PCIInfo(address="0000:00:1f.3"), 0, None) == [
        "--vfio",
        "/sys/bus/pci/devices/0000:00:1f.3,iommu=viommu,removable=true",
    ]


def test_vmm_args_acpi_table_crosvm() -> None:
    assert vmm_args_acpi_table({"type": "crosvm"}, "/run/nhlt.aml") == ["--acpi-table", "/run/nhlt.aml"]


def test_crosvm_pci_queries_use_configured_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    binaries: list[str | None] = []

    class FakeCrosvmLink:
        def __init__(self, _socket: str, crosvm_bin: str | None) -> None:
            binaries.append(crosvm_bin)

        async def is_pci_dev_connected(self, _pci_info: PCIInfo) -> bool:
            return True

        async def wait_until_pci_removed(self, _pci_info: PCIInfo) -> None:
            return

    monkeypatch.setattr(vmm_module, "CrosvmLink", FakeCrosvmLink)
    app_context = cast(
        AppContext,
        SimpleNamespace(config=SimpleNamespace(config={"general": {"crosvm": "/nix/store/crosvm/bin/crosvm"}})),
    )
    vm = {"type": "crosvm", "socket": "/run/net-vm.sock"}
    pci_info = PCIInfo(address="0000:00:14.3")

    assert asyncio.run(vmm_is_pci_dev_connected(app_context, vm, pci_info))
    asyncio.run(vmm_wait_until_removed(app_context, vm, pci_info))

    assert binaries == ["/nix/store/crosvm/bin/crosvm"] * 2


def test_pci_query_rejects_unknown_vmm() -> None:
    app_context = cast(AppContext, SimpleNamespace(config=SimpleNamespace(config={})))

    with pytest.raises(RuntimeError, match="Unsupported vm type"):
        asyncio.run(
            vmm_is_pci_dev_connected(
                app_context,
                {"type": "typo", "socket": "/run/unknown.sock"},
                PCIInfo(address="0000:00:14.3"),
            )
        )

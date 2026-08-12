import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from vhotplug import device as device_module
from vhotplug.appcontext import AppContext
from vhotplug.crosvmlink import CrosvmVMUnavailableError
from vhotplug.device import _attach_device_to_vm, _remove_device_from_vm
from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo


class FakeState:
    def __init__(self, current_vm: str | None = "app-vm") -> None:
        self.current_vm = current_vm
        self.removed = False
        self.attached_port: int | None = None

    def get_vm_for_device(self, _dev_info: USBInfo) -> str | None:
        return self.current_vm

    def get_crosvm_usb_port(self, _dev_info: USBInfo, _vm_name: str, _socket_path: str) -> int:
        return 7

    def remove_vm_for_device(self, _dev_info: USBInfo) -> None:
        self.removed = True

    def set_vm_for_device(self, _dev_info: USBInfo, vm_name: str) -> None:
        self.current_vm = vm_name

    def set_crosvm_usb_port(
        self,
        _dev_info: USBInfo,
        _vm_name: str,
        _socket_path: str,
        port: int | None,
    ) -> None:
        self.attached_port = port

    def clear_disconnected(self, _dev_info: USBInfo) -> bool:
        return False


def usb() -> USBInfo:
    return USBInfo(device_node="/dev/bus/usb/001/002", sys_name="1-2.1")


def app_context(state: FakeState) -> Any:
    config = SimpleNamespace(usb_authorization_enabled=lambda: False)
    return SimpleNamespace(dev_state=state, config=config, api_server=None)


def test_vm_unavailable_still_clears_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeState()

    async def unavailable(*_args: Any) -> None:
        raise CrosvmVMUnavailableError("VM unavailable")

    monkeypatch.setattr("vhotplug.device.vmm_remove_device", unavailable)

    asyncio.run(
        _remove_device_from_vm(
            app_context(state),
            usb(),
            {"name": "app-vm", "type": "crosvm", "socket": "/run/app-vm.sock"},
        )
    )

    assert state.removed


def test_detach_refusal_preserves_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeState()

    async def refuse(*_args: Any) -> None:
        raise RuntimeError("refusing to detach")

    monkeypatch.setattr("vhotplug.device.vmm_remove_device", refuse)

    with pytest.raises(RuntimeError, match="refusing"):
        asyncio.run(
            _remove_device_from_vm(
                app_context(state),
                usb(),
                {"name": "app-vm", "type": "crosvm", "socket": "/run/app-vm.sock"},
            )
        )

    assert not state.removed


def test_move_does_not_attach_when_old_vm_removal_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeState(current_vm="old-vm")
    attached = False

    async def remove(*_args: Any) -> None:
        raise RuntimeError("old VM detach failed")

    async def attach(*_args: Any) -> None:
        nonlocal attached
        attached = True

    monkeypatch.setattr("vhotplug.device.remove_device", remove)
    monkeypatch.setattr("vhotplug.device.vmm_add_device", attach)

    with pytest.raises(RuntimeError, match="old VM detach failed"):
        asyncio.run(
            _attach_device_to_vm(
                app_context(state),
                usb(),
                {"name": "new-vm", "type": "crosvm", "socket": "/run/new-vm.sock"},
            )
        )

    assert not attached


def test_crosvm_attach_passes_and_records_guest_port(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeState(current_vm=None)

    async def attach(*_args: Any) -> int:
        assert _args[-1] == 7
        return 9

    monkeypatch.setattr("vhotplug.device.vmm_add_device", attach)

    asyncio.run(
        _attach_device_to_vm(
            app_context(state),
            usb(),
            {"name": "app-vm", "type": "crosvm", "socket": "/run/app-vm.sock"},
        )
    )

    assert state.current_vm == "app-vm"
    assert state.attached_port == 9


def test_crosvm_pci_capability_is_checked_before_vfio_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeState(current_vm=None)
    vfio_setup = False

    async def unsupported(*_args: Any) -> None:
        raise RuntimeError("VFIO list is unavailable")

    def setup(*_args: Any) -> None:
        nonlocal vfio_setup
        vfio_setup = True

    monkeypatch.setattr(device_module, "vmm_check_pci_hotplug", unsupported)
    monkeypatch.setattr(device_module, "setup_vfio", setup)

    with pytest.raises(RuntimeError, match="VFIO list is unavailable"):
        asyncio.run(
            _attach_device_to_vm(
                app_context(state),
                PCIInfo(address="0000:00:14.3"),
                {"name": "net-vm", "type": "crosvm", "socket": "/run/net-vm.sock"},
            )
        )

    assert not vfio_setup


def pci_attach_context() -> tuple[AppContext, dict[str, Any]]:
    passthrough_info = SimpleNamespace(target_vm="net-vm", order=0)
    pci_info = PCIInfo(address="0000:00:14.3")
    device = {
        "pci_info": pci_info,
        "passthrough_info": passthrough_info,
        "current_vm": None,
        "iommu_member": False,
    }
    config = SimpleNamespace(get_vm=lambda _name: {"name": "net-vm", "type": "crosvm"})
    return cast(AppContext, SimpleNamespace(config=config)), device


def test_pci_resume_propagates_attach_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    app_context, device = pci_attach_context()
    monkeypatch.setattr(device_module, "_get_pci_devices", lambda *_args: [device])

    async def fail_attach(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Crosvm VFIO add failed")

    monkeypatch.setattr(device_module, "attach_device", fail_attach)

    with pytest.raises(RuntimeError, match=r"0000:00:14\.3.*Crosvm VFIO add failed"):
        asyncio.run(device_module.attach_connected_pci(app_context, fail_on_error=True))


def test_background_reconciliation_logs_attach_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    app_context, device = pci_attach_context()
    monkeypatch.setattr(device_module, "_get_pci_devices", lambda *_args: [device])

    async def fail_attach(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Crosvm VFIO add failed")

    monkeypatch.setattr(device_module, "attach_device", fail_attach)

    asyncio.run(device_module.attach_connected_pci(app_context))


def test_crosvm_args_isolate_hotplug_without_detected_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(
        get_vm=lambda _name: {"name": "net-vm", "type": "crosvm"},
        has_pci_passthrough_for_vm=lambda _name: True,
        get_acpi_tables=lambda _name: [],
    )
    app_context = cast(AppContext, SimpleNamespace(config=config))
    monkeypatch.setattr(device_module, "_get_pci_devices", lambda *_args: [])
    monkeypatch.setattr(device_module, "_get_evdev_devices", lambda *_args: [])

    assert device_module.get_vmm_args(app_context, "net-vm", None) == ["--vfio-isolate-hotplug"]


def test_crosvm_args_can_place_pci_device_on_root_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(
        get_vm=lambda _name: {"name": "gui-vm", "type": "crosvm"},
        has_pci_passthrough_for_vm=lambda _name: True,
        get_acpi_tables=lambda _name: [],
    )
    passthrough_info = SimpleNamespace(
        target_vm="gui-vm",
        auto_ovmf=False,
        qemu_use_root_bus=False,
        crosvm_use_root_bus=True,
    )
    device = {
        "pci_info": PCIInfo(address="0000:00:02.0"),
        "passthrough_info": passthrough_info,
    }
    app_context = cast(AppContext, SimpleNamespace(config=config))
    monkeypatch.setattr(device_module, "_get_pci_devices", lambda *_args: [device])
    monkeypatch.setattr(device_module, "_get_evdev_devices", lambda *_args: [])
    monkeypatch.setattr(device_module, "setup_vfio", lambda *_args: None)

    assert device_module.get_vmm_args(app_context, "gui-vm", None) == [
        "--vfio-isolate-hotplug",
        "--vfio",
        "/sys/bus/pci/devices/0000:00:02.0,iommu=viommu",
    ]


def test_attach_by_tag_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    received_fail_on_error = False

    async def attach(*_args: Any, **kwargs: Any) -> None:
        nonlocal received_fail_on_error
        received_fail_on_error = kwargs["fail_on_error"]

    monkeypatch.setattr(device_module, "attach_connected_pci", attach)

    asyncio.run(device_module.attach_existing_pci_devices_by_tag(cast(AppContext, object()), "audio"))

    assert received_fail_on_error


def test_explicit_pci_detach_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    pci_info = PCIInfo(address="0000:00:14.3")
    state = SimpleNamespace(
        list_pci_devices=lambda: [pci_info.address],
        get_vm_for_device=lambda _pci_info: "net-vm",
    )
    passthrough_info = SimpleNamespace(skip_on_suspend=False, tag=None)
    config = SimpleNamespace(vm_for_device=lambda _pci_info: passthrough_info)
    app_context = cast(AppContext, SimpleNamespace(dev_state=state, config=config))
    monkeypatch.setattr(device_module, "pci_info_by_address", lambda *_args: pci_info)

    async def fail_remove(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Crosvm VFIO remove failed")

    monkeypatch.setattr(device_module, "_remove_existing_device", fail_remove)

    with pytest.raises(RuntimeError, match="Crosvm VFIO remove failed"):
        asyncio.run(device_module.detach_connected_pci(app_context, fail_on_error=True))

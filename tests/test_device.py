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

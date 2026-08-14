import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from vhotplug.appcontext import AppContext
from vhotplug.devicestate import DeviceState
from vhotplug.usb import USBInfo
from vhotplug.vhotplug import reattach_devices_for_restarted_vms


def usb() -> USBInfo:
    return USBInfo(
        device_node="/dev/bus/usb/001/002",
        vid="0bda",
        pid="8153",
        serial="ethernet",
        sys_name="1-2.1",
    )


def test_queued_socket_event_preserves_current_usb_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    socket_path = tmp_path / "net-vm.sock"
    socket_path.touch()
    state = DeviceState(False, str(tmp_path / "vhotplug.state"))
    state.set_crosvm_usb_port(usb(), "net-vm", str(socket_path), 4)
    app_context = SimpleNamespace(
        config=SimpleNamespace(get_vm_by_socket=lambda path: {"name": "net-vm"} if path == str(socket_path) else None),
        dev_state=state,
    )
    calls: list[tuple[str, list[str]]] = []

    async def record(name: str, _app_context: Any, vm_names: list[str] | None = None) -> None:
        calls.append((name, vm_names or []))

    monkeypatch.setattr("vhotplug.vhotplug.attach_connected_evdev", lambda context: record("evdev", context))
    monkeypatch.setattr("vhotplug.vhotplug.attach_connected_pci", lambda context, names: record("pci", context, names))
    monkeypatch.setattr(
        "vhotplug.vhotplug.detach_disconnected_pci", lambda context, names: record("pci-detach", context, names)
    )
    monkeypatch.setattr("vhotplug.vhotplug.attach_connected_usb", lambda context, names: record("usb", context, names))

    asyncio.run(reattach_devices_for_restarted_vms(cast(AppContext, app_context), [str(socket_path)]))

    assert state.get_crosvm_usb_port(usb(), "net-vm", str(socket_path)) == 4
    assert calls == [
        ("evdev", []),
        ("pci", ["net-vm"]),
        ("pci-detach", ["net-vm"]),
        ("usb", ["net-vm"]),
    ]

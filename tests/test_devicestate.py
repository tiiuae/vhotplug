import json
import os
from pathlib import Path

import pytest

from vhotplug.devicestate import DeviceState
from vhotplug.usb import USBInfo


def usb(device_node: str = "/dev/bus/usb/001/002", vid: str = "046d") -> USBInfo:
    return USBInfo(device_node=device_node, vid=vid, pid="c52b", serial="serial", sys_name="1-2.1")


def test_crosvm_usb_port_is_scoped_to_vm_socket_generation(tmp_path: Path) -> None:
    state_path = tmp_path / "vhotplug.state"
    socket_path = tmp_path / "app-vm.sock"
    socket_path.touch()
    state = DeviceState(True, str(state_path))
    state.set_crosvm_usb_port(usb(), "app-vm", str(socket_path), 7)

    reloaded = DeviceState(True, str(state_path))
    assert reloaded.get_crosvm_usb_port(usb(), "app-vm", str(socket_path)) == 7

    socket_path.unlink()
    socket_path.touch()
    assert reloaded.get_crosvm_usb_port(usb(), "app-vm", str(socket_path)) is None


def test_crosvm_usb_port_uses_topology_and_validates_identity(tmp_path: Path) -> None:
    state_path = tmp_path / "vhotplug.state"
    socket_path = tmp_path / "app-vm.sock"
    socket_path.touch()
    state = DeviceState(True, str(state_path))
    state.set_crosvm_usb_port(usb(), "app-vm", str(socket_path), 7)

    renumbered = usb("/dev/bus/usb/001/009")
    assert state.get_crosvm_usb_port(renumbered, "app-vm", str(socket_path)) == 7
    assert state.get_crosvm_usb_port(usb(vid="1050"), "app-vm", str(socket_path)) is None


def test_crosvm_usb_port_is_removed_with_device_state(tmp_path: Path) -> None:
    state_path = tmp_path / "vhotplug.state"
    socket_path = tmp_path / "app-vm.sock"
    socket_path.touch()
    state = DeviceState(True, str(state_path))
    state.set_crosvm_usb_port(usb(), "app-vm", str(socket_path), 7)

    state.remove_vm_for_device(usb())

    assert state.get_crosvm_usb_port(usb(), "app-vm", str(socket_path)) is None


def test_crosvm_usb_port_is_scoped_to_vm(tmp_path: Path) -> None:
    state_path = tmp_path / "vhotplug.state"
    socket_path = tmp_path / "app-vm.sock"
    socket_path.touch()
    state = DeviceState(True, str(state_path))
    state.set_crosvm_usb_port(usb(), "app-vm", str(socket_path), 7)

    assert state.get_crosvm_usb_port(usb(), "other-vm", str(socket_path)) is None


def test_invalid_state_starts_empty(tmp_path: Path) -> None:
    state_path = tmp_path / "vhotplug.state"
    state_path.write_text('{"selected_vms":', encoding="utf-8")

    state = DeviceState(True, str(state_path))

    assert state.selected_vms == {}
    assert state.crosvm_usb_port_map == {}


def test_boolean_port_is_discarded(tmp_path: Path) -> None:
    state_path = tmp_path / "vhotplug.state"
    state_path.write_text(
        json.dumps(
            {
                "crosvm_usb_ports": {
                    "1-2.1": {
                        "vm": "app-vm",
                        "port": True,
                        "socket_generation": "1:2:3",
                        "vid": "046d",
                        "pid": "c52b",
                        "serial": None,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert DeviceState(True, str(state_path)).crosvm_usb_port_map == {}


def test_state_file_is_private(tmp_path: Path) -> None:
    state_path = tmp_path / "vhotplug.state"
    state = DeviceState(True, str(state_path))
    state.select_vm_for_device(usb(), "app-vm")

    assert os.stat(state_path).st_mode & 0o777 == 0o600


def test_state_save_failure_does_not_replace_valid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "vhotplug.state"
    state = DeviceState(True, str(state_path))
    state.select_vm_for_device(usb(), "app-vm")
    saved = state_path.read_text(encoding="utf-8")

    def fail_replace(_self: Path, _target: Path) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "replace", fail_replace)
    state.set_disconnected(usb())

    assert state_path.read_text(encoding="utf-8") == saved

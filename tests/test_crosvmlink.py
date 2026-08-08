import asyncio
from typing import Any

import pytest

from vhotplug.crosvmlink import CrosvmLink
from vhotplug.usb import USBInfo


class FakeProcess:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout.encode(), b""


def fake_crosvm(
    monkeypatch: pytest.MonkeyPatch,
    list_output: str = "devices",
    attach_output: str = "ok 2",
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    async def execute(*args: str, **_kwargs: Any) -> FakeProcess:
        calls.append(args)
        if args[1:3] == ("usb", "list"):
            return FakeProcess(list_output)
        if args[1:3] == ("usb", "attach"):
            return FakeProcess(attach_output)
        if args[1:3] == ("usb", "detach"):
            return FakeProcess(f"ok {args[3]}")
        raise AssertionError(f"Unexpected Crosvm command: {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    return calls


def usb() -> USBInfo:
    return USBInfo(device_node="/dev/bus/usb/001/002", vid="046d", pid="c52b")


def link(monkeypatch: pytest.MonkeyPatch) -> CrosvmLink:
    result = CrosvmLink("/run/app-vm.sock", "/nix/store/crosvm/bin/crosvm")
    monkeypatch.setattr(result, "_wait_for_boot", lambda: True)
    return result


def test_adds_identical_device_when_port_is_not_known(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, "devices 1 046d c52b")

    port = asyncio.run(link(monkeypatch).add_usb_device(usb()))

    assert port == 2
    assert any(command[1:3] == ("usb", "attach") for command in calls)


def test_reuses_persisted_port(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, "devices 2 046d c52b")

    port = asyncio.run(link(monkeypatch).add_usb_device(usb(), known_port=2))

    assert port == 2
    assert not any(command[1:3] == ("usb", "attach") for command in calls)


def test_removes_exact_port_among_identical_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, "devices 1 046d c52b 2 046d c52b")

    asyncio.run(link(monkeypatch).remove_usb_device(usb(), known_port=2))

    detach_commands = [command for command in calls if command[1:3] == ("usb", "detach")]
    assert detach_commands == [("/nix/store/crosvm/bin/crosvm", "usb", "detach", "2", "/run/app-vm.sock")]


def test_refuses_ambiguous_legacy_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, "devices 1 046d c52b 2 046d c52b")

    with pytest.raises(RuntimeError, match="guest port is unknown"):
        asyncio.run(link(monkeypatch).remove_usb_device(usb()))

    assert not any(command[1:3] == ("usb", "detach") for command in calls)


def test_no_available_port_does_not_detach_other_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, "devices 1 1234 5678", "no_available_port")
    crosvm = link(monkeypatch)
    crosvm.vm_retry_count = 0

    with pytest.raises(RuntimeError, match="Timeout"):
        asyncio.run(crosvm.add_usb_device(usb()))

    assert not any(command[1:3] == ("usb", "detach") for command in calls)

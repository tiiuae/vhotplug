import asyncio
from typing import Any, cast

import pytest

from vhotplug.crosvmlink import CrosvmLink, CrosvmVMUnavailableError
from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo


class FakeProcess:
    def __init__(self, stdout: str | bytes, returncode: int = 0, stderr: str | bytes = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        stdout = self.stdout.encode() if isinstance(self.stdout, str) else self.stdout
        stderr = self.stderr.encode() if isinstance(self.stderr, str) else self.stderr
        return stdout, stderr


def fake_crosvm(
    monkeypatch: pytest.MonkeyPatch,
    list_output: str | list[str] = "devices",
    attach_output: str = "ok 2",
    detach_output: str | None = None,
    list_returncode: int = 0,
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    list_outputs = [list_output] if isinstance(list_output, str) else list(list_output)

    async def execute(*args: str, **_kwargs: Any) -> FakeProcess:
        calls.append(args)
        if args[1:3] == ("usb", "list"):
            output = list_outputs.pop(0) if len(list_outputs) > 1 else list_outputs[0]
            return FakeProcess(output, list_returncode)
        if args[1:3] == ("usb", "attach"):
            return FakeProcess(attach_output)
        if args[1:3] == ("usb", "detach"):
            return FakeProcess(detach_output if detach_output is not None else f"ok {args[3]}")
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


def test_refuses_to_remove_different_device_from_known_port(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, "devices 2 1050 0407")

    with pytest.raises(RuntimeError, match="different device"):
        asyncio.run(link(monkeypatch).remove_usb_device(usb(), known_port=2))

    assert not any(command[1:3] == ("usb", "detach") for command in calls)


def test_refuses_ambiguous_legacy_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, "devices 1 046d c52b 2 046d c52b")

    with pytest.raises(RuntimeError, match="cannot be identified safely"):
        asyncio.run(link(monkeypatch).remove_usb_device(usb()))

    assert not any(command[1:3] == ("usb", "detach") for command in calls)


def test_malformed_ok_adopts_new_port_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, ["devices 1 1234 5678", "devices 1 1234 5678 2 046d c52b"], "ok")

    port = asyncio.run(link(monkeypatch).add_usb_device(usb()))

    assert port == 2
    assert len([command for command in calls if command[1:3] == ("usb", "attach")]) == 1


def test_malformed_list_port_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_crosvm(monkeypatch, "devices invalid 046d c52b")

    with pytest.raises(RuntimeError, match="invalid literal"):
        asyncio.run(link(monkeypatch).remove_usb_device(usb(), known_port=2))


def test_empty_detach_response_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_crosvm(monkeypatch, "devices 2 046d c52b", detach_output="")

    with pytest.raises(RuntimeError, match="empty response"):
        asyncio.run(link(monkeypatch).remove_usb_device(usb(), known_port=2))


def test_unavailable_vm_has_distinct_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_crosvm(monkeypatch, list_returncode=1)
    monkeypatch.setattr("vhotplug.crosvmlink.is_unix_socket_alive", lambda *_args: False)

    with pytest.raises(CrosvmVMUnavailableError):
        asyncio.run(link(monkeypatch).remove_usb_device(usb(), known_port=2))


def test_no_available_port_does_not_detach_other_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_crosvm(monkeypatch, "devices 1 1234 5678", "no_available_port")
    crosvm = link(monkeypatch)
    crosvm.vm_retry_count = 0

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(crosvm.add_usb_device(usb()))

    assert not any(command[1:3] == ("usb", "detach") for command in calls)


def pci() -> PCIInfo:
    return PCIInfo(address="0000:00:1f.3")


def fake_crosvm_vfio(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[str, ...]], set[str]]:
    calls: list[tuple[str, ...]] = []
    attached: set[str] = set()

    async def execute(*args: str, **_kwargs: Any) -> FakeProcess:
        calls.append(args)
        command = args[2]
        if command == "list":
            return FakeProcess("devices" + "".join(f" {path}" for path in sorted(attached)))
        path = args[3]
        if command == "add":
            attached.add(path)
        elif command == "remove":
            attached.discard(path)
        else:
            raise AssertionError(f"Unexpected Crosvm command: {args}")
        # Successful add/remove commands do not print a response; callers
        # verify completion with the subsequent list command.
        return FakeProcess("")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    return calls, attached


def test_pci_add_reconciles_against_crosvm_list(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, attached = fake_crosvm_vfio(monkeypatch)
    crosvm = link(monkeypatch)

    asyncio.run(crosvm.add_pci_device(pci()))
    asyncio.run(crosvm.add_pci_device(pci()))

    path = "/sys/bus/pci/devices/0000:00:1f.3"
    assert attached == {path}
    assert len([command for command in calls if command[1:3] == ("vfio", "add")]) == 1


def test_pci_add_is_not_reissued_while_waiting_for_list(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    add_accepted = False
    lists_after_add = 0
    path = "/sys/bus/pci/devices/0000:00:1f.3"

    async def execute(*args: str, **_kwargs: Any) -> FakeProcess:
        nonlocal add_accepted, lists_after_add
        calls.append(args)
        if args[1:3] == ("vfio", "add"):
            add_accepted = True
            return FakeProcess("")
        if args[1:3] == ("vfio", "list"):
            if add_accepted:
                lists_after_add += 1
            return FakeProcess(f"devices {path}" if lists_after_add >= 2 else "devices")
        raise AssertionError(f"Unexpected Crosvm command: {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    crosvm = link(monkeypatch)
    crosvm.vm_retry_timeout = 0

    asyncio.run(crosvm.add_pci_device(pci()))

    assert len([command for command in calls if command[1:3] == ("vfio", "add")]) == 1


def test_pci_remove_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, attached = fake_crosvm_vfio(monkeypatch)
    path = "/sys/bus/pci/devices/0000:00:1f.3"
    attached.add(path)
    crosvm = link(monkeypatch)

    asyncio.run(crosvm.remove_pci_device(pci()))
    asyncio.run(crosvm.remove_pci_device(pci()))

    assert not attached
    assert len([command for command in calls if command[1:3] == ("vfio", "remove")]) == 1


def test_pci_remove_is_not_reissued_while_waiting_for_list(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    remove_accepted = False
    lists_after_remove = 0
    path = "/sys/bus/pci/devices/0000:00:1f.3"

    async def execute(*args: str, **_kwargs: Any) -> FakeProcess:
        nonlocal remove_accepted, lists_after_remove
        calls.append(args)
        if args[1:3] == ("vfio", "remove"):
            remove_accepted = True
            return FakeProcess("")
        if args[1:3] == ("vfio", "list"):
            if remove_accepted:
                lists_after_remove += 1
            return FakeProcess("devices" if lists_after_remove >= 2 else f"devices {path}")
        raise AssertionError(f"Unexpected Crosvm command: {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    crosvm = link(monkeypatch)
    crosvm.vm_retry_timeout = 0

    asyncio.run(crosvm.remove_pci_device(pci()))

    assert len([command for command in calls if command[1:3] == ("vfio", "remove")]) == 1


def test_pci_add_retries_transient_list_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, attached = fake_crosvm_vfio(monkeypatch)
    original_execute: Any = asyncio.create_subprocess_exec
    failures = 1

    async def execute(*args: str, **kwargs: Any) -> FakeProcess:
        nonlocal failures
        if args[1:3] == ("vfio", "list") and failures:
            failures -= 1
            return FakeProcess("", returncode=1)
        return cast(FakeProcess, await original_execute(*args, **kwargs))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    crosvm = link(monkeypatch)
    crosvm.vm_retry_timeout = 0

    asyncio.run(crosvm.add_pci_device(pci()))

    assert attached == {"/sys/bus/pci/devices/0000:00:1f.3"}
    assert len([command for command in calls if command[1:3] == ("vfio", "add")]) == 1


def test_pci_remove_retries_failed_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, attached = fake_crosvm_vfio(monkeypatch)
    original_execute: Any = asyncio.create_subprocess_exec
    path = "/sys/bus/pci/devices/0000:00:1f.3"
    attached.add(path)
    failures = 1

    async def execute(*args: str, **kwargs: Any) -> FakeProcess:
        nonlocal failures
        if args[1:3] == ("vfio", "remove") and failures:
            calls.append(args)
            failures -= 1
            return FakeProcess("", returncode=1)
        return cast(FakeProcess, await original_execute(*args, **kwargs))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    crosvm = link(monkeypatch)
    crosvm.vm_retry_timeout = 0

    asyncio.run(crosvm.remove_pci_device(pci()))

    assert not attached
    assert len([command for command in calls if command[1:3] == ("vfio", "remove")]) == 2


def test_pci_list_rejects_failed_command(monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess("", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    with pytest.raises(RuntimeError, match="VFIO list failed"):
        asyncio.run(link(monkeypatch).pci_list())


def test_pci_list_rejects_unexpected_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess("devices none attached")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    with pytest.raises(RuntimeError, match="Unexpected entries"):
        asyncio.run(link(monkeypatch).pci_list())


def test_vfio_failure_reports_stdout_and_replaces_invalid_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(*_args: str, **_kwargs: Any) -> FakeProcess:
        return FakeProcess(b"\xffdevice busy", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    with pytest.raises(RuntimeError, match=r"app-vm\.sock.*device busy"):
        asyncio.run(link(monkeypatch).pci_list())


def test_wait_until_pci_removed_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _calls, attached = fake_crosvm_vfio(monkeypatch)
    attached.add("/sys/bus/pci/devices/0000:00:1f.3")
    crosvm = link(monkeypatch)
    crosvm.vm_retry_count = 0

    with pytest.raises(RuntimeError, match="Timed out waiting"):
        asyncio.run(crosvm.wait_until_pci_removed(pci()))


def test_vfio_command_timeout_terminates_client(monkeypatch: pytest.MonkeyPatch) -> None:
    class HangingProcess:
        returncode = None

        def __init__(self) -> None:
            self.killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            return 1

    process = HangingProcess()

    async def execute(*_args: str, **_kwargs: Any) -> HangingProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    crosvm = link(monkeypatch)
    crosvm.vfio_command_timeout = 0.01

    with pytest.raises(RuntimeError, match="VFIO list timed out"):
        asyncio.run(crosvm.pci_list())

    assert process.killed

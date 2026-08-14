import os
import socket
import time
from types import SimpleNamespace

import pytest

from vhotplug import misc


def test_wait_for_unix_socket_retries_within_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def socket_alive(_socket_path: str, _sock_type: socket.SocketKind) -> bool:
        nonlocal attempts
        attempts += 1
        return attempts == 2

    monkeypatch.setattr(misc, "is_unix_socket_alive", socket_alive)
    monkeypatch.setattr(os.path, "exists", lambda _path: True)
    monkeypatch.setattr(os, "stat", lambda _path: SimpleNamespace(st_ctime=0))
    monkeypatch.setattr(time, "time", lambda: 0)
    sleep_intervals: list[float] = []
    monkeypatch.setattr(time, "sleep", sleep_intervals.append)

    assert misc.wait_for_unix_socket("/run/vm.sock", 1, 0, socket.SOCK_STREAM, poll_interval=0.1)
    assert attempts == 2
    assert sleep_intervals == [0.1]


def test_wait_for_unix_socket_does_not_wait_for_missing_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os.path, "exists", lambda _path: False)
    monkeypatch.setattr(time, "sleep", lambda _seconds: pytest.fail("Unexpected sleep"))

    assert not misc.wait_for_unix_socket("/run/vm.sock", 1, 0, socket.SOCK_STREAM, poll_interval=0.1)

# SPDX-FileCopyrightText: 2022-2026 TII (SSRC) and the Ghaf contributors
# SPDX-License-Identifier: Apache-2.0

from typing import Any, cast

import pytest

from vhotplugcli.apiclient import APIClient
from vhotplugcli.vhotplugcli import vmm_args


class FakeClient:
    def __init__(self, response: dict[str, Any] | None = None, error: RuntimeError | None = None) -> None:
        self.response = response
        self.error = error
        self.require_pci: bool | None = None

    def connect(self) -> None:
        if self.error is not None:
            raise self.error

    def vmm_args(
        self,
        _vm: str,
        _qemu_bus_prefix: str | None,
        _qemu_bus_start_index: int | None,
        require_pci: bool,
    ) -> dict[str, Any]:
        self.require_pci = require_pci
        assert self.response is not None
        return self.response


def test_vmm_args_forwards_required_pci(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient({"result": "ok", "vmm_args": ["--vfio", "/sys/device"]})

    vmm_args(cast(APIClient, client), "net-vm", None, 1, timeout=0, require_pci=True)

    assert client.require_pci is True
    assert capsys.readouterr().out == "--vfio /sys/device"


def test_vmm_args_connection_timeout() -> None:
    client = FakeClient(error=RuntimeError("daemon unavailable"))

    with pytest.raises(RuntimeError, match="Timed out waiting for vhotplug"):
        vmm_args(cast(APIClient, client), "net-vm", None, 1, timeout=0, require_pci=True)

import time
from pathlib import Path

import pytest

from vhotplug import pci


def test_wait_for_vfio_device_node_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reproduces a real race: writing to drivers_probe schedules vfio-pci's probe
    # but does not guarantee it has finished, so /dev/vfio/<group> can briefly not
    # exist yet. The caller (VM start) must wait for it rather than assume it is
    # there as soon as drivers_probe has been written.
    attempts = 0

    def fake_exists(self: Path) -> bool:
        nonlocal attempts
        if self.name == "iommu_group":
            return True
        attempts += 1
        return attempts == 2

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "resolve", lambda _self: Path("/sys/kernel/iommu_groups/7"))
    sleep_intervals: list[float] = []
    monkeypatch.setattr(time, "sleep", sleep_intervals.append)

    pci._wait_for_vfio_device_node("0000:00:02.0")

    assert attempts == 2
    assert sleep_intervals == [0.1]


def test_wait_for_vfio_device_node_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_exists(self: Path) -> bool:
        # iommu_group is present; /dev/vfio/<group> never appears.
        return self.name == "iommu_group"

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "resolve", lambda _self: Path("/sys/kernel/iommu_groups/7"))
    sleep_intervals: list[float] = []
    monkeypatch.setattr(time, "sleep", sleep_intervals.append)

    pci._wait_for_vfio_device_node("0000:00:02.0")

    assert sleep_intervals == [0.1, 0.1, 0.1, 0.1]


def test_wait_for_vfio_device_node_returns_immediately_when_iommu_group_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "exists", lambda _self: False)
    monkeypatch.setattr(time, "sleep", lambda _seconds: pytest.fail("Unexpected sleep"))

    pci._wait_for_vfio_device_node("0000:00:02.0")

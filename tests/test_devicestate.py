from pathlib import Path

from vhotplug.devicestate import DeviceState
from vhotplug.usb import USBInfo


def test_crosvm_usb_port_is_persisted_and_removed(tmp_path: Path) -> None:
    state_path = tmp_path / "vhotplug.state"
    usb = USBInfo(device_node="/dev/bus/usb/001/002")
    state = DeviceState(True, str(state_path))
    state.set_crosvm_usb_port(usb, 7)

    reloaded = DeviceState(True, str(state_path))
    assert reloaded.get_crosvm_usb_port(usb) == 7

    reloaded.remove_vm_for_device(usb)
    assert DeviceState(True, str(state_path)).get_crosvm_usb_port(usb) is None

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from vhotplug.config import Config
from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo, get_usb_info


def test_input() -> None:
    config = Config("config.json")
    res = config.vm_for_device(USBInfo(interfaces=":030101:030102:030000:"))
    assert res is not None and res.target_vm == "vm1" and res.allowed_vms is None


def test_pci_passthrough_rule_targets_vm() -> None:
    config = Config("config.json")

    assert config.has_pci_passthrough_for_vm("vm1")
    assert not config.has_pci_passthrough_for_vm("vm2")


def test_input_ignore_vid_pid() -> None:
    config = Config("config.json")
    res = config.vm_for_device(
        USBInfo(
            vid="046d",
            pid="c52b",
            vendor_name="Logitech",
            product_name="USB_Receiver",
            interfaces=":030101:030102:030000:",
        )
    )
    assert res is None


def test_ethernet_product_name() -> None:
    config = Config("config.json")
    res = config.vm_for_device(USBInfo(product_name="Some ethernet device"))
    assert res is not None and res.target_vm == "vm1" and res.allowed_vms is None


def test_ethernet_ignore_vid_pid() -> None:
    config = Config("config.json")
    res = config.vm_for_device(
        USBInfo(
            vid="0b95",
            pid="1790",
            vendor_name="ASIX_Elec._Corp.",
            product_name="AX88179",
            interfaces=":ffff00:",
        )
    )
    assert res is None


def test_disabled() -> None:
    config = Config("config.json")
    res = config.vm_for_device(USBInfo(vid="067b", pid="23a3"))
    assert res is None


def test_audio() -> None:
    config = Config("config.json")
    res = config.vm_for_device(USBInfo(interfaces=":010100:"))
    assert res is not None and res.target_vm == "vm1" and res.allowed_vms is None


def test_audio_and_video() -> None:
    config = Config("config.json")
    res = config.vm_for_device(USBInfo(interfaces=":010100:0e0100:"))
    assert res is not None and res.target_vm is None and res.allowed_vms == ["vm1", "vm2"]


def test_webcam() -> None:
    config = Config("config.json")
    res = config.vm_for_device(
        USBInfo(
            vid="04f2",
            pid="b751",
            vendor_name="Chicony_Electronics_Co._Ltd.",
            product_name="Integrated_Camera",
            interfaces=":0e0100:0e0200:0e0101:0e0201:fe0101:",
        )
    )
    assert res is not None and res.target_vm is None and res.allowed_vms == ["vm1", "vm2"]


def test_ssd() -> None:
    config = Config("config.json")
    res = config.vm_for_device(
        USBInfo(
            vid="04e8",
            pid="61f5",
            vendor_name="Samsung",
            product_name="Portable_SSD_T5",
            interfaces=":080650:080662:",
        )
    )
    assert res is None


def test_hub() -> None:
    config = Config("config.json")
    res = config.vm_for_device(
        USBInfo(
            vid="1d6b",
            pid="0002",
            vendor_name="Linux_6.12.33_xhci-hcd",
            product_name="xHCI_Host_Controller",
            interfaces=":090000:",
        )
    )
    assert res is None


def test_bluetooth() -> None:
    config = Config("config.json")
    res = config.vm_for_device(
        USBInfo(
            vid="0bda",
            pid="4852",
            vendor_name="Realtek Semiconductor Corp.",
            product_name="Bluetooth_Radio",
            device_class=224,
            device_subclass=1,
            device_protocol=1,
            interfaces=":e00101:",
        )
    )
    assert res is not None and res.target_vm == "vm2" and res.allowed_vms is None


def test_bus_port() -> None:
    config = Config("config.json")
    res = config.vm_for_device(USBInfo(busnum=11, ports=[22, 33, 44]))
    assert res is not None and res.target_vm == "vm2" and res.allowed_vms is None


def test_wrong_bus_port() -> None:
    config = Config("config.json")
    res = config.vm_for_device(USBInfo(busnum=11, ports=[33, 22, 44]))
    assert res is None


def test_usb_removable(tmp_path: Path) -> None:
    config_path = tmp_path / "usb-removable.json"
    config_path.write_text(
        json.dumps(
            {
                "usbPassthrough": [
                    {
                        "targetVm": "vm1",
                        "allow": [{"productName": "Integrated.*", "removable": ["fixed", "unknown"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = Config(str(config_path))

    assert config.vm_for_device(USBInfo(product_name="Integrated Camera", removable="fixed")) is not None
    assert config.vm_for_device(USBInfo(product_name="Integrated Camera", removable="FIXED")) is not None
    assert config.vm_for_device(USBInfo(product_name="Integrated Camera", removable="unknown")) is not None
    assert config.vm_for_device(USBInfo(product_name="Integrated Camera", removable="removable")) is None
    assert config.vm_for_device(USBInfo(product_name="Integrated Camera")) is None
    assert config.vm_for_device(USBInfo(product_name="External Camera", removable="fixed")) is None


def test_usb_removable_is_not_a_standalone_match(tmp_path: Path) -> None:
    config_path = tmp_path / "usb-removable-only.json"
    config_path.write_text(
        json.dumps(
            {
                "usbPassthrough": [
                    {
                        "targetVm": "vm1",
                        "allow": [{"removable": ["fixed"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert Config(str(config_path)).vm_for_device(USBInfo(removable="fixed")) is None


def test_usb_removable_string_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "usb-removable-string.json"
    config_path.write_text(
        json.dumps(
            {
                "usbPassthrough": [
                    {
                        "targetVm": "vm1",
                        "allow": [{"productName": "Integrated.*", "removable": "fixed"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert Config(str(config_path)).vm_for_device(USBInfo(product_name="Integrated Camera", removable="fixed")) is None


def test_get_usb_removable_attribute() -> None:
    device = SimpleNamespace(
        device_node="/dev/bus/usb/001/002",
        properties={"BUSNUM": "1", "DEVNUM": "2"},
        attributes={"removable": b"fixed\n"},
        sys_name="1-2",
        sys_path="/sys/devices/usb1/1-2",
    )

    usb_info = get_usb_info(cast(Any, device))

    assert usb_info.removable == "fixed"
    assert "removable" not in usb_info.to_dict()


def test_pci_rule_auto_ovmf(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "pciPassthrough": [
                    {
                        "description": "NVIDIA GPU for VM1",
                        "targetVm": "vm1",
                        "autoOvmf": True,
                        "allow": [{"vendorId": "10de", "deviceId": "2206"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = Config(str(config_path))
    res = config.vm_for_device(PCIInfo(address="0000:01:00.0", vendor_id=0x10DE, device_id=0x2206))
    assert res is not None and res.auto_ovmf


def test_get_ovmf_paths() -> None:
    config = Config("config.json")
    assert config.get_ovmf_code() == "/usr/share/OVMF/OVMF_CODE.fd"
    assert config.get_ovmf_vars() == "/usr/share/OVMF/OVMF_VARS.fd"


def test_pci_rule_qemu_use_root_bus(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "pciPassthrough": [
                    {
                        "description": "GPU for VM1",
                        "targetVm": "vm1",
                        "qemuUseRootBus": True,
                        "allow": [{"vendorId": "10de", "deviceId": "2206"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = Config(str(config_path))
    res = config.vm_for_device(PCIInfo(address="0000:01:00.0", vendor_id=0x10DE, device_id=0x2206))
    assert res is not None and res.qemu_use_root_bus


def test_pci_rule_crosvm_use_root_bus(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "pciPassthrough": [
                    {
                        "description": "Integrated GPU for VM1",
                        "targetVm": "vm1",
                        "crosvmUseRootBus": True,
                        "allow": [{"vendorId": "8086", "deviceId": "7d45"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = Config(str(config_path))
    res = config.vm_for_device(PCIInfo(address="0000:00:02.0", vendor_id=0x8086, device_id=0x7D45))
    assert res is not None and res.crosvm_use_root_bus


def test_usb_authorization(tmp_path: Path) -> None:
    config = Config("config.json")
    assert not config.usb_authorization_enabled()
    assert not config.usb_authorization_deauthorize_unmatched()

    config_path = tmp_path / "usb-authorization.json"
    config_path.write_text(json.dumps({"usbAuthorization": {"enable": True}}), encoding="utf-8")
    assert Config(str(config_path)).usb_authorization_enabled()

    config_path.write_text(
        json.dumps(
            {
                "usbAuthorization": {
                    "enable": True,
                    "deauthorizeUnmatched": True,
                    "hostAllow": [{"vendorId": "1234", "productId": "5678"}],
                }
            }
        ),
        encoding="utf-8",
    )
    host_config = Config(str(config_path))
    assert host_config.usb_authorization_enabled()
    assert host_config.usb_authorization_deauthorize_unmatched()
    assert host_config.usb_authorization_host_allowed(USBInfo(vid="1234", pid="5678"))
    assert not host_config.usb_authorization_host_allowed(USBInfo(vid="1234", pid="abcd"))

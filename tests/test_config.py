from vhotplug.config import Config
from vhotplug.usb import USBInfo

def test_input():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            interfaces=":030101:030102:030000:"
        )
    )
    assert vm[0] == "vm1"

def test_input_ignore_vid_pid():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            vid="046d",
            pid="c52b",
            vendor_name="Logitech",
            product_name="USB_Receiver",
            interfaces=":030101:030102:030000:"
        )
    )
    assert vm is None

def test_ethernet_product_name():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            product_name="Some ethernet device"
        )
    )
    assert vm[0] == "vm1"

def test_ethernet_ignore_vid_pid():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            vid="0b95",
            pid="1790",
            vendor_name="ASIX_Elec._Corp.",
            product_name="AX88179",
            interfaces=":ffff00:"
        )
    )
    assert vm is None

def test_disabled():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            vid="067b",
            pid="23a3"
        )
    )
    assert vm is None

def test_audio():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            interfaces=":010100:"
        )
    )
    assert vm[0] == "vm1"

def test_audio_and_video():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            interfaces=":010100:0e0100:"
        )
    )
    assert vm[1] == ["vm1", "vm2"]

def test_webcam():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            vid="04f2",
            pid="b751",
            vendor_name="Chicony_Electronics_Co._Ltd.",
            product_name="Integrated_Camera",
            interfaces=":0e0100:0e0200:0e0101:0e0201:fe0101:"
        )
    )
    assert vm[1] == ["vm1", "vm2"]

def test_ssd():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            vid="04e8",
            pid="61f5",
            vendor_name="Samsung",
            product_name="Portable_SSD_T5",
            interfaces=":080650:080662:"
        )
    )
    assert vm is None

def test_hub():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
        USBInfo(
            vid="1d6b",
            pid="0002",
            vendor_name="Linux_6.12.33_xhci-hcd",
            product_name="xHCI_Host_Controller",
            interfaces=":090000:",
        )
    )
    assert vm is None

def test_bluetooth():
    config = Config("config.json")
    vm = config.vm_for_usb_device(
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
    assert vm[0] == "vm2"

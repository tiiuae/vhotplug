from vhotplug.usb import USBInfo


def test_get_modaliases_full_info() -> None:
    info = USBInfo(
        vid="413c",
        pid="81e0",
        device_class=0,
        device_subclass=0,
        device_protocol=0,
        bcd_device=0x0100,
        interfaces=":030101:",
    )
    aliases = info.get_modaliases()
    assert aliases == ["usb:v413Cp81E0d0100dc00dsc00dp00ic03isc01ip01in00"]


def test_get_modaliases_missing_bcd_device() -> None:
    # Reproduces a real crash: a device re-enumerating during a host resume can
    # report bcdDevice as unavailable, leaving USBInfo.bcd_device as None. Before
    # the fix, get_modaliases() -> _modalias() raised
    # TypeError: unsupported format string passed to NoneType.__format__
    info = USBInfo(
        vid="413c",
        pid="81e0",
        device_class=0,
        device_subclass=0,
        device_protocol=0,
        bcd_device=None,
        interfaces=":030101:",
    )
    assert info.get_modaliases() == []


def test_get_modaliases_missing_device_class() -> None:
    info = USBInfo(
        vid="413c",
        pid="81e0",
        device_class=None,
        device_subclass=0,
        device_protocol=0,
        bcd_device=0x0100,
        interfaces=":030101:",
    )
    assert info.get_modaliases() == []


def test_get_modaliases_no_vid_pid() -> None:
    info = USBInfo(interfaces=":030101:")
    assert info.get_modaliases() == []

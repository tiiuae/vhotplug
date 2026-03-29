import pytest

from vhotplug.pci import PCIInfo, pci_is_nvidia_gpu
from vhotplug.vmm import vmm_args_ovmf


def test_pci_is_nvidia_gpu_display_controller() -> None:
    dev = PCIInfo(address="0000:01:00.0", vendor_id=0x10DE, pci_class=0x03)
    assert pci_is_nvidia_gpu(dev)


def test_pci_is_nvidia_gpu_audio_function() -> None:
    dev = PCIInfo(address="0000:01:00.1", vendor_id=0x10DE, pci_class=0x04)
    assert not pci_is_nvidia_gpu(dev)


def test_vmm_args_ovmf() -> None:
    vm = {
        "type": "qemu",
    }
    assert vmm_args_ovmf(vm, "/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd") == [
        "-drive",
        "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd",
        "-drive",
        "if=pflash,format=raw,unit=1,readonly=on,file=/usr/share/OVMF/OVMF_VARS.fd",
    ]


def test_vmm_args_ovmf_requires_paths() -> None:
    with pytest.raises(RuntimeError, match="ovmfCode"):
        vmm_args_ovmf({"type": "qemu"}, None, None)

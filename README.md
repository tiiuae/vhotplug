# vhotplug

This application runs as a service on the host system, listening for device add and remove events using libudev.
It dynamically adds USB devices to a QEMU virtual machine via the QEMU Machine Protocol (QMP) sockets using the official qemu.qmp library.

# Features

- Automatically detects when devices are added or removed from the host.
- Integrates with QEMU virtual machines to add or remove devices dynamically.
- Uses libudev for device monitoring on the host.
- No extra udev configuration is required.
- Different device types can be assigned to different virtual machines.
- Supports evdev passthrough (virtio-input-host-pci) of non-USB input devices.

# Example

```
sudo python3 ./main.py --add-connected --qmp-input /var/run/qmp.sock
```

# Usage

```
usage: main.py [-h] [--add-connected | --no-add-connected]
               [--add-evdev | --no-add-evdev] [--pcie-bus-prefix BUSPREFIX]
               [--qmp-input QMPINPUT] [--qmp-sound QMPSOUND]
               [--qmp-disk QMPDISK] [--qmp-net QMPNET]
               [-d | --debug | --no-debug]

Hot-plugging USB devices to the virtual machines

options:
  -h, --help            show this help message and exit
  --add-connected, --no-add-connected
                        Add already connected devices on startup (default:
                        False)
  --add-evdev, --no-add-evdev
                        Add non-USB input devices using evdev passthrough
                        (default: False)
  --pcie-bus-prefix BUSPREFIX
                        PCIe bus prefix for evdev passthrough
  --qmp-input QMPINPUT  Enable hot-plugging of input devices using the
                        specified QMP socket
  --qmp-sound QMPSOUND  Enable hot-plugging of sound devices using the
                        specified QMP socket
  --qmp-disk QMPDISK    Enable hot-plugging of disk devices using the
                        specified QMP socket
  --qmp-net QMPNET      Enable hot-plugging of network devices using the
                        specified QMP socket
  -d, --debug, --no-debug
                        Enable debug messages (default: False)
```

# License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.

# Contributing

If you would like to contribute to this project, please fork the repository and submit a pull request with your changes.

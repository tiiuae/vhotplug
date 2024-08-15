# vhotplug

This application runs as a service on the host system, listening for device add and remove events using libudev.
It dynamically adds USB devices to a QEMU virtual machines via the QEMU Machine Protocol (QMP) sockets using the official qemu.qmp library.

# Features

- Automatically detects when devices are added or removed from the host.
- Integrates with QEMU virtual machines to add or remove devices dynamically.
- Uses libudev for device monitoring on the host.
- No extra udev configuration is required.
- Different device types can be assigned to different virtual machines.
- Supports evdev passthrough (virtio-input-host-pci) of non-USB input devices.

# Example

```
sudo python3 -m vhotplug -a -c ./vhotplug.conf
```

# Usage

```
usage: vhotplug [-h] -c CONFIG
                [-a | --attach-connected | --no-attach-connected]
                [-d | --debug | --no-debug]

Hot-plugging USB devices to the virtual machines

options:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        Path to the configuration file
  -a, --attach-connected, --no-attach-connected
                        Attach connected devices on startup (default: False)
  -d, --debug, --no-debug
                        Enable debug messages (default: False)
```

# Sample config

```json
{
    "vms": [
        {
            "name": "vm1",
            "qmpSocket": "/tmp/qmp-socket1",
            "usbPassthrough": [
                {
                    "class": 3,
                    "protocol": 2,
                    "description": "HID Mouse",
                    "ignore": [
                        {
                            "vendorId": "046d",
                            "productId": "c52b",
                            "description": "Logitech, Inc. Unifying Receiver"
                        }
                    ]
                },
                {
                    "productName": ".*ethernet.*",
                    "description": "Ethernet devices",
                    "ignore": [
                        {
                            "vendorId": "0b95",
                            "productId": "1790",
                            "description": "AX88179 Gigabit Ethernet"
                        }
                    ]
                },
                {
                    "vendorId": "067b",
                    "productId": "23a3",
                    "description": "Prolific USB-to-Serial Bridge",
                    "disable": true
                }
            ],
            "evdevPassthrough": {
                "enable": false,
                "pcieBusPrefix": "ep"
            }
        }
    ]
}
```

# License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.

# Contributing

If you would like to contribute to this project, please fork the repository and submit a pull request with your changes.

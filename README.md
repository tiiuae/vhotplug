# vhotplug

This application runs as a service on the host, monitors device add/remove events using libudev and dynamically attaches USB devices to virtual machines based on rules defined in a configuration file.

# Features

- Automatically detects when devices are added or removed from the host.
- Integrates with QEMU and crosvm to add or remove devices in real time.
- Uses libudev for device monitoring on the host.
- No extra udev configuration is required.
- Different device types can be assigned to different virtual machines.
- Supports evdev passthrough (virtio-input-host-pci) of non-USB input devices for QEMU.

# Rule Matching

Device assignment is based on rules defined in the configuration file.
Each rule can match devices using one or more of the following parameters:
- vendorId — USB vendor ID (e.g., "0bda")
- productId — USB product ID (e.g., "4852")
- vendorName — Vendor name (from udev or USB database, supports regular expressions)
- productName — Product name (from udev or USB database, supports regular expressions)
- interfaceClass — USB interface class (e.g., 224)
- interfaceSubclass — USB interface subclass (e.g., 1)
- interfaceProtocol — USB interface protocol (e.g., 1)
- deviceClass — USB device class (e.g., 224)
- deviceSubclass — USB device subclass (e.g., 1)
- deviceProtocol — USB device protocol (e.g., 1)

The same parameters can also be used in ignore rules to explicitly exclude certain devices from being passed through.
Only the fields present in a rule are used for matching. If multiple rules match a device, the first match is used.

Note: Many USB devices are composite devices, meaning they expose multiple interfaces. When matching against interfaceClass, interfaceSubclass and interfaceProtocol, it is sufficient for at least one interface to match the rule.
In practice, matching by interfaces is often more reliable than using device_class, since many real-world USB devices leave the device-level class fields unset or use generic values like 0 (defined at the interface level instead).

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
            "type": "qemu",
            "socket": "/tmp/qmp-socket1",
            "usbPassthrough": [
                {
                    "interfaceClass": 3,
                    "interfaceProtocol": 2,
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

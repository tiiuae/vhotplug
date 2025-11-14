# vhotplug

This application runs as a service on the host, monitors device add/remove events using libudev and dynamically attaches devices to virtual machines based on rules defined in a configuration file.

# Features

- Automatically detects when devices are added or removed from the host.
- Integrates with QEMU and crosvm to add or remove devices in real time.
- Uses libudev for device monitoring on the host.
- No extra udev configuration is required.
- Different device types can be assigned to different virtual machines.
- Supports USB and PCI devices.
- Supports evdev passthrough (virtio-input-host-pci) of non-USB input devices for QEMU.

# Rule Matching

Device assignment is based on rules defined in the configuration file. Each rule can match devices using one or more parameters.
The same parameters can be used in allow and deny rule sets. Only the fields present in a rule are used for matching. If multiple rules match a device, the first match is used.

# USB Devices

The following parameters can be used for USB passthrough:

- vendorId — USB vendor ID (e.g., "0bda")
- productId — USB product ID (e.g., "4852")
- vendorName — Vendor name (from udev or USB database, supports regular expressions)
- productName — Product name (from udev or USB database, supports regular expressions)
- bus - USB bus number
- port - USB root port
- interfaceClass — USB interface class (e.g., 224)
- interfaceSubclass — USB interface subclass (e.g., 1)
- interfaceProtocol — USB interface protocol (e.g., 1)
- deviceClass — USB device class (e.g., 224)
- deviceSubclass — USB device subclass (e.g., 1)
- deviceProtocol — USB device protocol (e.g., 1)

Note: Many USB devices are composite devices, meaning they expose multiple interfaces. When matching against interfaceClass, interfaceSubclass and interfaceProtocol, it is sufficient for at least one interface to match the rule.
In practice, matching by interfaces is often more reliable than using deviceClass, since many real-world USB devices leave the device-level class fields unset or use generic values like 0 (defined at the interface level instead).

# PCI Devices

The following parameters can be used for PCI passthrough:

- address — PCI address (e.g. "0000:00:14.3")
- vendorId — PCI vendor ID (e.g., "8086")
- deviceId — PCI device ID (e.g., "a7a1")
- deviceClass — PCI device class (e.g., 2)
- deviceSubclass — PCI device subclass (e.g., 128)
- deviceProgIf — PCI device programming interface (e.g., 0)

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
  "usbPassthrough": [
    {
      "description": "Devices for VM1",
      "targetVm": "vm1",
      "allow": [
        {
          "interfaceClass": 3,
          "interfaceProtocol": 2,
          "description": "HID Mouse"
        },
        {
          "productName": ".*ethernet.*",
          "description": "Ethernet devices"
        },
        {
          "vendorId": "067b",
          "productId": "23a3",
          "description": "Prolific USB-to-Serial Bridge",
          "disable": true
        }
      ],
      "deny": [
        {
          "vendorId": "046d",
          "productId": "c52b",
          "description": "Logitech, Inc. Unifying Receiver"
        },
        {
          "vendorId": "0b95",
          "productId": "1790",
          "description": "AX88179 Gigabit Ethernet"
        }
      ]
    }
  ],
  "pciPassthrough": [
    {
      "description": "Devices for VM1",
      "targetVm": "vm1",
      "allow": [
        {
          "address": "0000:00:14.3",
          "description": "Intel WiFi card"
        },
        {
          "vendorId": "8086",
          "deviceId": "a7a1",
          "description": "Intel Iris GPU"
        }
      ]
    }
  ],
  "evdevPassthrough": {
    "disable": true,
    "targetVm": "vm1"
  },
  "vms": [
    {
      "name": "vm1",
      "type": "qemu",
      "socket": "/tmp/qmp-socket1"
    }
  ]
}
```

# Installation

## With pip

### User Installation

To install vhotplug as a regular user (requires Python >= 3.13):

```bash
pip install .
```

This will install vhotplug and its dependencies to your Python environment.

### Developer Installation

For development work, install in editable mode so changes to the source code are immediately reflected:

```bash
pip install -e .
```

This allows you to modify the code and test changes without reinstalling.

# Getting Started with Nix

This project uses Nix for reproducible builds and development environments.

## Building

Build the package:

```bash
nix build
```

## Running

Run vhotplug directly without installing:

```bash
nix run . -- --config ./config.json --debug
```

## Development

Enter the development shell with all dependencies:

```bash
nix develop
```

Inside the dev shell, you have access to:

- `vhotplug` - The main application
- `pytest` - Run tests
- `mypy` - Type checking
- `ruff` - Code linting and formatting
- `treefmt` - Format all code (Nix, Python, JSON, Markdown)

Format and check code:

```bash
nix fmt
```

Run tests:

```bash
nix build .#checks.x86_64-linux.vhotplug-service
```

## NixOS Module

Use vhotplug as a NixOS module in your configuration:

```nix
{
  inputs.vhotplug.url = "github:tiiuae/vhotplug";

  outputs = { nixpkgs, vhotplug, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [
        vhotplug.nixosModules.default
        {
          services.vhotplug = {
            enable = true;
            attachConnected = true;
            debug = false;
            config = {
              usbPassthrough = [
                {
                  description = "USB devices for VM";
                  targetVm = "myvm";
                  allow = [
                    {
                      interfaceClass = 3;
                      description = "HID devices";
                    }
                  ];
                }
              ];
              vms = [
                {
                  name = "myvm";
                  type = "qemu";
                  socket = "/run/qemu/vm.sock";
                }
              ];
            };
          };
        }
      ];
    };
  };
}
```

# License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.

# Contributing

If you would like to contribute to this project, please fork the repository and submit a pull request with your changes.

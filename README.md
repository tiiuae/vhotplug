# vhotplug

This application runs as a service on the host system, listening for device add and remove events using libudev.
It dynamically adds USB devices to a QEMU virtual machine via the QEMU Machine Protocol (QMP) socket using the official qemu.qmp library.

# Features

- Automatically detects when devices are added or removed from the host.
- Integrates with QEMU virtual machines to add or remove devices dynamically.
- Uses libudev for device monitoring on the host.
- No extra udev configuration is required.

# Usage

Run the application with the path to the QMP socket:

```
sudo python3 ./main.py --add-connected --qmp-socket /var/run/qmp.sock
```

# License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.

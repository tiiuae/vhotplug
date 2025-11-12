# Overview

The API provides a mechanism for managing USB device hotplug events and controlling their attachment to virtual machines.
Communication is performed over JSON-encoded messages exchanged through either a TCP or VSOCK connection.

- Each message must be a valid JSON object encoded in UTF-8 and terminated with a newline (\n).
- Each request sent by the client results in a synchronous response from vhotplug.
- Every response contains a result field with value "ok" or "failed".
- In case of "ok", responses may include additional fields depending on the request.
- In case of "failed", an additional error field must be present.
- Clients may enable asynchronous notifications from vhotplug.
- The transport may use either TCP or VSOCK, as configured.

# Configuration

API is enabled by adding the following parameters to the "general" section in the configuration file:

```
"api": {
    "enable": true,
    "host": "0.0.0.0",
    "port": 2000,
    "unixSocket": "/var/lib/vhotplug/vhotplug.sock",
    "transports": ["tcp", "vsock", "unix"],
    "allowedCids": [3, 4, 5]
}
```

Supported transports: "tcp", "vsock", "unix".

# USB device

A USB device is uniquely identified by its device node, which consists of the USB bus and address.

When a USB device is represented in the API, it may contain the following fields:

```
{
    "device_node": "/dev/bus/usb/003/078",
    "vid": "1111",
    "pid": "2222",
    "vendor_name": "Manufacturer",
    "product_name": "Product",
    "allowed_vms": ["vm1", "vm2"],
    "vm": "vm1",
    ...
}
```

Depending on the message, extra fields may be present or some fields may be missing.
The device_node field is the only one guaranteed to be present.

# Messages

## Enable notifications

Request: `{"action": "enable_notifications"}`

Response: `{"result": "ok"}`

## Get a list of USB devices

Request: `{"action": "usb_list"}`

Response: `{"result": "ok", "usb_devices": [{...}, {...}]}`

## Attach a USB device to a VM

### Attach using device node

Request: `{"action": "usb_attach", "device_node": "/dev/bus/usb/003/078", "vm": "vm1"}`

Response: `{"result": "ok"}`

### Attach using bus and port

Request: `{"action": "usb_attach", "bus": "1", "port": "2", "vm": "vm1"}`

Response: `{"result": "ok"}`

### Attach using vendor ID and product ID

Request: `{"action": "usb_attach", "vid": "1111", "pid": "2222", "vm": "vm1"}`

Response: `{"result": "ok"}`

## Detach a USB device from a VM

### Detach using device node

Request: `{"action": "usb_detach", "device_node": "/dev/bus/usb/003/078"}`

Response: `{"result": "ok"}`

### Detach using bus and port

Request: `{"action": "usb_detach", "bus": "1", "port": "2"}`

Response: `{"result": "ok"}`

### Detach using vendor ID and product ID

Request: `{"action": "usb_detach", "vid": "1111", "pid": "2222"}`

Response: `{"result": "ok"}`

## Get a list of PCI devices

Request: `{"action": "pci_list"}`

Response: `{"result": "ok", "pci_devices": [{...}, {...}]}`

## Attach a PCI device to a VM

### Attach using address

Request: `{"action": "pci_attach", "address": "0000:00:01.0", "vm": "vm1"}`

Response: `{"result": "ok"}`

### Attach using vendor ID and device ID

Request: `{"action": "pci_attach", "vid": "1111", "did": "2222", "vm": "vm1"}`

Response: `{"result": "ok"}`

## Detach a PCI device from a VM

### Detach using PCI address

Request: `{"action": "pci_detach", "address": "0000:00:01.0"}`

Response: `{"result": "ok"}`

### Detach using vendor ID and device ID

Request: `{"action": "pci_detach", "vid": "1111", "did": "2222"}`

Response: `{"result": "ok"}`

# Notifications

## USB device connected to host

`{"event": "usb_connected", "usb_device": {...}}`

## USB device disconnected from host

`{"event": "usb_disconnected", "usb_device": {...}}`

## USB device attached to a VM

`{"event": "usb_attached", "usb_device": {...}, "vm": "vm1"}`

## USB device detached from a VM

`{"event": "usb_detached", "usb_device": {...}, "vm": "vm1"}`

## VM needs to be selected for a USB device

`{"event": "usb_select_vm", "usb_device": {...}, "allowed_vms": ["vm1", "vm2"]}`

## PCI device attached to a VM

`{"event": "pci_attached", "pci_device": {...}, "vm": "vm1"}`

## PCI device detached from a VM

`{"event": "pci_detached", "pci_device": {...}, "vm": "vm1"}`

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyudev

if TYPE_CHECKING:
    from vhotplug.apiserver import APIServer
    from vhotplug.config import Config
    from vhotplug.devicestate import DeviceState


@dataclass
class AppContext:
    config: "Config"
    udev_monitor: pyudev.Monitor
    udev_context: pyudev.Context
    dev_state: "DeviceState"
    api_server: "APIServer | None" = None

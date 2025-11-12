import socket
import json
import logging
import time
from typing import Any, Callable
from collections.abc import Mapping

logger = logging.getLogger(__name__)

# pylint: disable=too-many-public-methods
class APIClient:
    # pylint: disable=too-many-positional-arguments
    def __init__(self, host: str = "127.0.0.1", port: int = 2000, cid: int = 2, transport: str = "vsock", path: str = "/var/lib/vhotplug/vhotplug.sock") -> None:
        self.transport = transport
        self.host = host
        self.port = port
        self.cid = cid
        self.path = path
        self.sock: socket.socket | None = None

    def clone(self) -> "APIClient":
        return APIClient(
            transport=self.transport,
            host=self.host,
            port=self.port,
            cid=self.cid,
            path=self.path,
        )

    def connect(self) -> None:
        try:
            if self.transport == "vsock":
                if not self.cid or not self.port:
                    raise ValueError("VSOCK CID and port are required")

                logger.debug("Connecting to vsock cid %s on port %s", self.cid, self.port)
                self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
                self.sock.connect((self.cid, self.port))
            elif self.transport == "tcp":
                if not self.host or not self.port:
                    raise ValueError("TCP host and port are required")

                logger.debug("Connecting to tcp host %s on port %s", self.host, self.port)
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.connect((self.host, self.port))
            elif self.transport == "unix":
                if not self.path:
                    raise ValueError("Unix socket path is required")

                logger.debug("Connecting to unix socket %s", self.path)
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(self.path)
            else:
                raise ValueError("API transport must be either vsock, tcp or unix")

        except OSError as e:
            raise RuntimeError(f"Failed to connect: {str(e)}") from e
        logger.debug("Connected")

    def send(self, msg: Mapping[str, Any]) -> dict[str, Any]:
        data = json.dumps(msg) + "\n"
        logger.debug("Sending: %s", data)
        assert self.sock is not None, "Socket is not connected"
        self.sock.sendall(data.encode("utf-8"))
        res = self.recv()
        logger.debug("Received: %s", res)
        return res

    def recv(self) -> dict[str, Any]:
        buffer = ""
        assert self.sock is not None, "Socket is not connected"
        while True:
            data = self.sock.recv(4096)
            if not data:
                raise RuntimeError("API connection closed by remote")
            buffer += data.decode("utf-8")
            while "\n" in buffer:
                msg, buffer = buffer.split("\n", 1)
                try:
                    result: dict[str, Any] = json.loads(msg)
                    return result
                except ValueError:
                    logger.error("Invalid JSON in API response: %s", msg)

    def close(self) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None

    def enable_notifications(self) -> None:
        response = self.send({"action": "enable_notifications"})
        if response.get("result") != "ok":
            logger.error("Failed to enable notifications: %s", response)

    def usb_list(self) -> dict[str, Any]:
        return self.send({"action": "usb_list"})

    def usb_attach(self, device_node: str, vm: str) -> dict[str, Any]:
        return self.send({"action": "usb_attach", "device_node": device_node, "vm": vm})

    def usb_attach_by_bus_port(self, bus: int, port: int, vm: str) -> dict[str, Any]:
        return self.send({"action": "usb_attach", "bus": bus, "port": port, "vm": vm})

    def usb_attach_by_vid_pid(self, vid: str, pid: str, vm: str) -> dict[str, Any]:
        return self.send({"action": "usb_attach", "vid": vid, "pid": pid, "vm": vm})

    def usb_detach(self, device_node: str) -> dict[str, Any]:
        return self.send({"action": "usb_detach", "device_node": device_node})

    def usb_detach_by_bus_port(self, bus: int, port: int) -> dict[str, Any]:
        return self.send({"action": "usb_detach", "bus": bus, "port": port})

    def usb_detach_by_vid_pid(self, vid: str, pid: str) -> dict[str, Any]:
        return self.send({"action": "usb_detach", "vid": vid, "pid": pid})

    def usb_suspend(self, vm: str) -> dict[str, Any]:
        return self.send({"action": "usb_suspend", "vm": vm})

    def usb_resume(self, vm: str) -> dict[str, Any]:
        return self.send({"action": "usb_resume", "vm": vm})

    def pci_list(self) -> dict[str, Any]:
        return self.send({"action": "pci_list"})

    def pci_attach(self, address: str, vm: str) -> dict[str, Any]:
        return self.send({"action": "pci_attach", "address": address, "vm": vm})

    def pci_attach_by_vid_did(self, vid: str, did: str, vm: str) -> dict[str, Any]:
        return self.send({"action": "pci_attach", "vid": vid, "did": did, "vm": vm})

    def pci_detach(self, address: str) -> dict[str, Any]:
        return self.send({"action": "pci_detach", "address": address})

    def pci_detach_by_vid_did(self, vid: str, did: str) -> dict[str, Any]:
        return self.send({"action": "pci_detach", "vid": vid, "did": did})

    def pci_suspend(self, vm: str) -> dict[str, Any]:
        return self.send({"action": "pci_suspend", "vm": vm})

    def pci_resume(self, vm: str) -> dict[str, Any]:
        return self.send({"action": "pci_resume", "vm": vm})

    def recv_notifications(self, callback: Callable[[dict[str, Any]], None], reconnect_delay: int = 3) -> None:
        while True:
            try:
                client = self.clone()
                client.connect()
                client.enable_notifications()

                buffer = ""
                assert client.sock is not None, "Socket is not connected"
                while True:
                    data = client.sock.recv(4096)
                    if not data:
                        raise ConnectionError("API connection for notifications closed by remote")
                    buffer += data.decode("utf-8")
                    while "\n" in buffer:
                        msg, buffer = buffer.split("\n", 1)
                        try:
                            parsed = json.loads(msg)
                            callback(parsed)
                        except ValueError:
                            logger.error("Invalid JSON in API notification: %s", msg)
            except OSError as e:
                logger.warning("Notification listener error: %s", e)
                logger.warning("Reconnecting in %s sec...", reconnect_delay)
            finally:
                client.close()
                time.sleep(reconnect_delay)

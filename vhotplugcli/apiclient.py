import socket
import json
import logging
import time

logger = logging.getLogger(__name__)

# pylint: disable=too-many-public-methods
class APIClient:
    # pylint: disable=too-many-positional-arguments
    def __init__(self, host="127.0.0.1", port=2000, cid=2, transport="vsock", path="/var/lib/vhotplug/vhotplug.sock"):
        self.transport = transport
        self.host = host
        self.port = port
        self.cid = cid
        self.path = path
        self.sock = None

    def clone(self):
        return APIClient(
            transport=self.transport,
            host=self.host,
            port=self.port,
            cid=self.cid,
            path=self.path,
        )

    def connect(self):
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

    def send(self, msg):
        data = json.dumps(msg) + "\n"
        logger.debug("Sending: %s", data)
        self.sock.sendall(data.encode("utf-8"))
        res = self.recv()
        logger.debug("Received: %s", res)
        return res

    def recv(self):
        buffer = ""
        while True:
            data = self.sock.recv(4096)
            if not data:
                raise RuntimeError("API connection closed by remote")
            buffer += data.decode("utf-8")
            while "\n" in buffer:
                msg, buffer = buffer.split("\n", 1)
                try:
                    return json.loads(msg)
                except ValueError:
                    logger.error("Invalid JSON in API response: %s", msg)
        return None

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def enable_notifications(self):
        response = self.send({"action": "enable_notifications"})
        if response.get("result") != "ok":
            logger.error("Failed to enable notifications: %s", response)

    def usb_list(self):
        return self.send({"action": "usb_list"})

    def usb_attach(self, device_node, vm):
        return self.send({"action": "usb_attach", "device_node": device_node, "vm": vm})

    def usb_attach_by_bus_port(self, bus, port, vm):
        return self.send({"action": "usb_attach", "bus": bus, "port": port, "vm": vm})

    def usb_attach_by_vid_pid(self, vid, pid, vm):
        return self.send({"action": "usb_attach", "vid": vid, "pid": pid, "vm": vm})

    def usb_detach(self, device_node):
        return self.send({"action": "usb_detach", "device_node": device_node})

    def usb_detach_by_bus_port(self, bus, port):
        return self.send({"action": "usb_detach", "bus": bus, "port": port})

    def usb_detach_by_vid_pid(self, vid, pid):
        return self.send({"action": "usb_detach", "vid": vid, "pid": pid})

    def usb_suspend(self):
        return self.send({"action": "usb_suspend"})

    def usb_resume(self):
        return self.send({"action": "usb_resume"})

    def pci_list(self):
        return self.send({"action": "pci_list"})

    def pci_attach(self, address, vm):
        return self.send({"action": "pci_attach", "address": address, "vm": vm})

    def pci_attach_by_vid_did(self, vid, did, vm):
        return self.send({"action": "pci_attach", "vid": vid, "did": did, "vm": vm})

    def pci_detach(self, address):
        return self.send({"action": "pci_detach", "address": address})

    def pci_detach_by_vid_did(self, vid, did):
        return self.send({"action": "pci_detach", "vid": vid, "did": did})

    def pci_suspend(self):
        return self.send({"action": "pci_suspend"})

    def pci_resume(self):
        return self.send({"action": "pci_resume"})

    def recv_notifications(self, callback, reconnect_delay=3):
        while True:
            try:
                client = self.clone()
                client.connect()
                client.enable_notifications()

                buffer = ""
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

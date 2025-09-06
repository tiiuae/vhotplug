import socket
import threading
import json
import logging
import asyncio
import pyudev
from vhotplug.device import get_usb_devices, vm_for_usb_device, attach_usb_device, remove_usb_device
from vhotplug.usb import get_usb_info

logger = logging.getLogger("vhotplug")

class APIServer:
    # pylint: disable = too-many-instance-attributes
    def __init__(self, config, context, loop):
        self.loop = loop
        self.config = config
        self.context = context
        api_config = config.config.get("general", {}).get("api", {})
        self.transport = api_config.get("transport", "vsock")
        self.host = api_config.get("host", "127.0.0.1")
        self.port = api_config.get("port", 2000)
        self.cid = socket.VMADDR_CID_ANY
        self.server_socket = None
        self.running = False
        self.clients = []
        self.notify_clients = []
        self.clients_lock = threading.Lock()
        self.client_threads = []

    def start(self):
        if self.transport == "vsock":
            self.server_socket = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.cid, self.port))
            logger.info("API server listening on VSOCK port %s", self.port)
        elif self.transport == "tcp":
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            logger.info("API server listening on TCP port %s, host: %s", self.port, self.host)
        else:
            raise ValueError("API transport must be either vsock or tcp")

        self.server_socket.listen()
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self):
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        with self.clients_lock:
            for client_sock in self.clients:
                client_sock.close()
            self.clients.clear()
            self.notify_clients.clear()
        for t in self.client_threads:
            t.join()
        logger.info("API server stopped")

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, client_addr = self.server_socket.accept()
                logger.info("API client connected %s", client_addr)
                with self.clients_lock:
                    self.clients.append(client_sock)
                t = threading.Thread(target=self._client_handler, args=(client_sock, client_addr), daemon=True)
                t.start()
                self.client_threads.append(t)
            except OSError as e:
                if self.running:
                    logger.error("API accept error: %s", e)

    def _client_handler(self, client_sock, client_addr):
        buffer = ""
        try:
            with client_sock:
                while self.running:
                    data = client_sock.recv(4096)
                    if not data:
                        logger.info("API client disconnected: %s", client_addr)
                        break

                    buffer += data.decode("utf-8")
                    while "\n" in buffer:
                        raw_msg, buffer = buffer.split("\n", 1)
                        try:
                            msg = json.loads(raw_msg)
                            res = self.handle_message(client_sock, client_addr, msg)
                            self._send(client_sock, res)
                        except (TypeError, ValueError) as e:
                            logger.error("Invalid JSON from %s: %s, error %s", client_addr, raw_msg, e)
        except OSError as e:
            logger.error("API client %s receive failed: %s", client_addr, e)
        finally:
            with self.clients_lock:
                if client_sock in self.clients:
                    self.clients.remove(client_sock)
                if client_sock in self.notify_clients:
                    self.notify_clients.remove(client_sock)

    def _send(self, client_sock, msg):
        try:
            data = json.dumps(msg) + "\n"
            client_sock.sendall(data.encode("utf-8"))
        except (OSError) as e:
            logger.error("API send failed (OS error): %s", e)
        except (TypeError, ValueError) as e:
            logger.error("API send failed (JSON error): %s", e)

    def notify(self, msg):
        logger.debug("Sending notification: %s", msg)
        with self.clients_lock:
            for client_sock in self.notify_clients:
                self._send(client_sock, msg)

    def notify_usb_attached(self, usb_info, vm_name):
        self.notify({"event": "usb_attached", "usb_device": usb_info.to_dict(), "vm": vm_name})

    def notify_usb_detached(self, usb_info, vm_name):
        self.notify({"event": "usb_detached", "usb_device": {"device_node": usb_info.device_node}, "vm": vm_name})

    def notify_usb_select_vm(self, usb_info, allowed_vms):
        self.notify({"event": "usb_select_vm", "usb_device": usb_info.to_dict(), "allowed_vms": allowed_vms})

    # pylint: disable = too-many-return-statements
    def handle_message(self, client_sock, client_addr, msg):
        msg_name = msg.get("action")
        if msg_name == "enable_notifications":
            logger.info("Enabling notifications for %s", client_addr)
            with self.clients_lock:
                self.notify_clients.append(client_sock)
            return {"result": "ok"}
        if msg_name == "usb_list":
            logger.info("API request usb list from %s", client_addr)
            return {"result": "ok", "usb_devices": get_usb_devices(self.context, self.config)}
        if msg_name == "usb_attach":
            logger.info("API request usb attach from %s", client_addr)
            try:
                device_node = msg.get("device_node")
                vm_name = msg.get("vm")
                logger.info("Request to attach %s to %s", device_node, vm_name)

                # Find USB device in the system
                device = pyudev.Devices.from_device_file(self.context, device_node)
                usb_info = get_usb_info(device)

                # Check that target VM is allowed for this device
                vm_coro = vm_for_usb_device(self.context, self.config, self, usb_info, vm_name, False)
                future = asyncio.run_coroutine_threadsafe(vm_coro, self.loop)
                vm = future.result()
                if not vm:
                    raise RuntimeError("VM not found")

                # Attach
                coro = attach_usb_device(self.config, self, usb_info, vm)
                future = asyncio.run_coroutine_threadsafe(coro, self.loop)
                future.result()
                return {"result": "ok"}
            except pyudev.DeviceNotFoundError:
                raise RuntimeError("USB device not found") from None
            except RuntimeError as e:
                logger.error("Failed to attach device: %s", e)
                return {"result": "failed", "error": str(e)}
        if msg_name == "usb_detach":
            logger.info("API request usb detach from %s", client_addr)
            try:
                device_node = msg.get("device_node")
                logger.info("Request to detach %s", device_node)

                # Find USB device in the system
                device = pyudev.Devices.from_device_file(self.context, device_node)
                usb_info = get_usb_info(device)

                # Remove
                coro = remove_usb_device(self.config, usb_info, self)
                future = asyncio.run_coroutine_threadsafe(coro, self.loop)
                future.result()
                return {"result": "ok"}
            except pyudev.DeviceNotFoundError:
                raise RuntimeError("USB device not found") from None
            except RuntimeError as e:
                logger.error("Failed to detach device: %s", e)
                return {"result": "failed", "error":  str(e)}

        logger.warning("API server unknown message: %s", msg_name)
        return {"result": "failed", "error": f"Unknown message: {msg_name}"}

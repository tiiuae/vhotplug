import socket
import threading
import json
import logging
import asyncio
from vhotplug.device import get_usb_devices, attach_existing_usb_device, remove_existing_usb_device

logger = logging.getLogger("vhotplug")

class APIServer:
    # pylint: disable = too-many-instance-attributes
    def __init__(self, app_context, loop):
        self.loop = loop
        self.app_context = app_context
        api_config = self.app_context.config.config.get("general", {}).get("api", {})
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

        match msg_name:
            case "enable_notifications":
                logger.info("Enabling notifications for %s", client_addr)
                with self.clients_lock:
                    self.notify_clients.append(client_sock)
                return {"result": "ok"}

            case "usb_list":
                logger.info("API request usb list from %s", client_addr)
                return {"result": "ok", "usb_devices": get_usb_devices(self.app_context)}

            case "usb_attach":
                logger.info("API request usb attach from %s", client_addr)
                try:
                    device_node = msg.get("device_node")
                    selected_vm = msg.get("vm")
                    logger.info("Request to attach %s to %s", device_node, selected_vm)
                    asyncio.run_coroutine_threadsafe(
                        attach_existing_usb_device(self.app_context, device_node, selected_vm),
                        self.loop,
                    ).result()
                    return {"result": "ok"}
                except RuntimeError as e:
                    logger.error("Failed to attach device: %s", e)
                    return {"result": "failed", "error": str(e)}

            case "usb_detach":
                logger.info("API request usb detach from %s", client_addr)
                try:
                    device_node = msg.get("device_node")
                    logger.info("Request to detach %s", device_node)
                    asyncio.run_coroutine_threadsafe(
                        remove_existing_usb_device(self.app_context, device_node),
                        self.loop,
                    ).result()
                    return {"result": "ok"}
                except RuntimeError as e:
                    logger.error("Failed to detach device: %s", e)
                    return {"result": "failed", "error": str(e)}

            case _:
                logger.warning("API server unknown message: %s", msg_name)
                return {"result": "failed", "error": f"Unknown message: {msg_name}"}

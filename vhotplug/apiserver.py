import socket
import threading
import json
import logging
import asyncio
import os
from vhotplug.device import (get_usb_devices, attach_existing_usb_device, attach_existing_usb_device_by_bus_port, attach_existing_usb_device_by_vid_pid,
    remove_existing_usb_device, remove_existing_usb_device_by_bus_port, remove_existing_usb_device_by_vid_pid,
    attach_connected_devices, detach_connected_devices
)

logger = logging.getLogger("vhotplug")

class APIServer:
    # pylint: disable = too-many-instance-attributes
    def __init__(self, app_context, loop):
        self.loop = loop
        self.app_context = app_context
        api_config = self.app_context.config.config.get("general", {}).get("api", {})
        self.transports = api_config.get("transports", [])
        self.host = api_config.get("host", "127.0.0.1")
        self.port = api_config.get("port", 2000)
        self.allowed_cids = api_config.get("allowedCids")
        self.cid = socket.VMADDR_CID_ANY
        self.uds_path = api_config.get("unix_socket", "/var/lib/vhotplug/vhotplug.sock")
        self.server_sockets = []
        self.running = False
        self.clients = []
        self.notify_clients = []
        self.clients_lock = threading.Lock()
        self.client_threads = []

        self.handlers = {
            "enable_notifications": self._on_enable_notifications,
            "usb_list": self._on_usb_list,
            "usb_attach": self._on_usb_attach,
            "usb_detach": self._on_usb_detach,
            "usb_suspend": self._on_usb_suspend,
            "usb_resume": self._on_usb_resume,
        }

    def start(self):
        self.running = True
        for transport in self.transports:
            if transport == "vsock":
                sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.cid, self.port))
                logger.info("API server listening on VSOCK port %s", self.port)
            elif transport == "tcp":
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, self.port))
                logger.info("API server listening on TCP port %s, host: %s", self.port, self.host)
            elif transport == "unix":
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    os.unlink(self.uds_path)
                except OSError:
                    if os.path.exists(self.uds_path):
                        raise
                sock.bind(self.uds_path)
                logger.info("API server listening on UNIX socket %s", self.uds_path)
            else:
                raise ValueError("API transport must be either vsock, tcp or unix")

            sock.listen()
            self.server_sockets.append(sock)
            threading.Thread(target=self._accept_loop, args=(sock, transport), daemon=True).start()

    def stop(self):
        self.running = False
        for sock in self.server_sockets:
            sock.close()
        with self.clients_lock:
            for client_sock in self.clients:
                client_sock.close()
            self.clients.clear()
            self.notify_clients.clear()
        for t in self.client_threads:
            t.join()
        self.client_threads.clear()
        if "unix" in self.transports and os.path.exists(self.uds_path):
            os.unlink(self.uds_path)
        logger.info("API server stopped")

    def _accept_loop(self, server_socket, transport):
        while self.running:
            try:
                client_sock, client_addr = server_socket.accept()
                logger.debug("API client connected: %s", client_addr)
                if transport == "vsock" and self.allowed_cids:
                    remote_cid, _ = client_addr
                    if remote_cid not in self.allowed_cids:
                        logger.warning("Rejected VSOCK client with CID %s", remote_cid)
                        client_sock.close()
                        continue

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
        if not client_addr and client_sock.family == socket.AF_UNIX:
            client_addr = "unix socket"
        try:
            with client_sock:
                while self.running:
                    data = client_sock.recv(4096)
                    if not data:
                        logger.debug("API client disconnected: %s", client_addr)
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

    def handle_message(self, client_sock, client_addr, msg):
        action = msg.get("action")
        handler = self.handlers.get(action)
        if handler is None:
            logger.warning("Unknown API request %s from %s", action, client_addr)
            return {"result": "failed", "error": f"Unknown message: {action}"}
        try:
            logger.info('API request "%s" from %s', action, client_addr)
            return handler(client_sock, client_addr, msg)
        except (RuntimeError, TypeError, ValueError) as e:
            logger.error("Failed to prcoess API request: %s", e)
            return {"result": "failed", "error": str(e)}

    def _on_enable_notifications(self, client_sock, _client_addr, _msg):
        with self.clients_lock:
            if client_sock not in self.notify_clients:
                self.notify_clients.append(client_sock)
        return {"result": "ok"}

    def _on_usb_list(self, _client_sock, _client_addr, _msg):
        return {"result": "ok", "usb_devices": get_usb_devices(self.app_context)}

    def _on_usb_attach(self, _client_sock, _client_addr, msg):
        device_node = msg.get("device_node")
        bus = msg.get("bus")
        port = msg.get("port")
        vid = msg.get("vid")
        pid = msg.get("pid")
        selected_vm = msg.get("vm")
        if device_node:
            logger.info("Request to attach %s to %s", device_node, selected_vm)
            asyncio.run_coroutine_threadsafe(
                attach_existing_usb_device(self.app_context, device_node, selected_vm),
                self.loop,
            ).result()
        elif bus and port:
            logger.info("Request to attach by bus %s and port %s to %s", bus, port, selected_vm)
            asyncio.run_coroutine_threadsafe(
                attach_existing_usb_device_by_bus_port(self.app_context, bus, port, selected_vm),
                self.loop,
            ).result()
        else:
            logger.info("Request to attach by vid %s and pid %s to %s", vid, pid, selected_vm)
            asyncio.run_coroutine_threadsafe(
                attach_existing_usb_device_by_vid_pid(self.app_context, vid, pid, selected_vm),
                self.loop,
            ).result()

        return {"result": "ok"}

    def _on_usb_detach(self, _client_sock, _client_addr, msg):
        device_node = msg.get("device_node")
        bus = msg.get("bus")
        port = msg.get("port")
        vid = msg.get("vid")
        pid = msg.get("pid")
        if device_node:
            logger.info("Request to detach %s", device_node)
            asyncio.run_coroutine_threadsafe(
                remove_existing_usb_device(self.app_context, device_node, True),
                self.loop,
            ).result()
        elif bus and port:
            logger.info("Request to detach by bus %s and port %s", bus, port)
            asyncio.run_coroutine_threadsafe(
                remove_existing_usb_device_by_bus_port(self.app_context, bus, port, True),
                self.loop,
            ).result()
        else:
            logger.info("Request to detach by vid %s and pid %s", vid, pid)
            asyncio.run_coroutine_threadsafe(
                remove_existing_usb_device_by_vid_pid(self.app_context, vid, pid, True),
                self.loop,
            ).result()

        return {"result": "ok"}

    def _on_usb_suspend(self, _client_sock, _client_addr, _msg):
        asyncio.run_coroutine_threadsafe(
            detach_connected_devices(self.app_context),
            self.loop,
        ).result()
        return {"result": "ok"}

    def _on_usb_resume(self, _client_sock, _client_addr, _msg):
        asyncio.run_coroutine_threadsafe(
            attach_connected_devices(self.app_context),
            self.loop,
        ).result()
        return {"result": "ok"}

import asyncio
import json
import logging
import os
import socket
import threading
from collections.abc import Callable
from typing import Any

from vhotplug.appcontext import AppContext
from vhotplug.device import (
    attach_connected_pci,
    attach_connected_usb,
    attach_existing_pci_device,
    attach_existing_pci_device_by_vid_did,
    attach_existing_usb_device,
    attach_existing_usb_device_by_bus_port,
    attach_existing_usb_device_by_vid_pid,
    detach_connected_pci,
    detach_connected_usb,
    get_pci_devices,
    get_usb_devices,
    remove_existing_pci_device,
    remove_existing_pci_device_by_vid_did,
    remove_existing_usb_device,
    remove_existing_usb_device_by_bus_port,
    remove_existing_usb_device_by_vid_pid,
)
from vhotplug.pci import PCIInfo
from vhotplug.usb import USBInfo

logger = logging.getLogger("vhotplug")


class APIServer:
    def __init__(self, app_context: AppContext, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.app_context = app_context
        api_config = self.app_context.config.config.get("general", {}).get("api", {})
        self.transports = api_config.get("transports", [])
        self.host = api_config.get("host", "127.0.0.1")
        self.port = api_config.get("port", 2000)
        self.allowed_cids = api_config.get("allowedCids")
        self.cid = socket.VMADDR_CID_ANY
        self.uds_path = api_config.get("unixSocket", "/var/lib/vhotplug/vhotplug.sock")
        self.server_sockets: list[socket.socket] = []
        self.running = False
        self.clients: list[socket.socket] = []
        self.notify_clients: list[socket.socket] = []
        self.clients_lock = threading.Lock()
        self.client_threads: list[threading.Thread] = []

        self.handlers: dict[str, Callable[[socket.socket, Any, dict[str, Any]], dict[str, Any]]] = {
            "enable_notifications": self._on_enable_notifications,
            "usb_list": self._on_usb_list,
            "usb_attach": self._on_usb_attach,
            "usb_detach": self._on_usb_detach,
            "usb_suspend": self._on_usb_suspend,
            "usb_resume": self._on_usb_resume,
            "pci_list": self._on_pci_list,
            "pci_attach": self._on_pci_attach,
            "pci_detach": self._on_pci_detach,
            "pci_suspend": self._on_pci_suspend,
            "pci_resume": self._on_pci_resume,
            "disconnected_list": self._on_disconnected_list,
        }

    def start(self) -> None:
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
                logger.info(
                    "API server listening on TCP port %s, host: %s",
                    self.port,
                    self.host,
                )
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

    def stop(self) -> None:
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

    def _accept_loop(self, server_socket: socket.socket, transport: str) -> None:
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
                t = threading.Thread(
                    target=self._client_handler,
                    args=(client_sock, client_addr),
                    daemon=True,
                )
                t.start()
                self.client_threads.append(t)
            except OSError:
                if self.running:
                    logger.exception("API accept error")

    def _client_handler(self, client_sock: socket.socket, client_addr: Any) -> None:
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
                        except (TypeError, ValueError):
                            logger.exception("Invalid JSON from %s: %s", client_addr, raw_msg)
        except OSError:
            logger.exception("API client %s receive failed", client_addr)
        finally:
            with self.clients_lock:
                if client_sock in self.clients:
                    self.clients.remove(client_sock)
                if client_sock in self.notify_clients:
                    self.notify_clients.remove(client_sock)

    def _send(self, client_sock: socket.socket, msg: dict[str, Any]) -> None:
        try:
            data = json.dumps(msg) + "\n"
            client_sock.sendall(data.encode("utf-8"))
        except OSError:
            logger.exception("API send failed (OS error)")
        except (TypeError, ValueError):
            logger.exception("API send failed (JSON error)")

    def notify(self, msg: dict[str, Any]) -> None:
        logger.debug("Sending notification: %s", msg)
        with self.clients_lock:
            for client_sock in self.notify_clients:
                self._send(client_sock, msg)

    def notify_usb_attached(self, usb_info: USBInfo, vm_name: str) -> None:
        self.notify({"event": "usb_attached", "usb_device": usb_info.to_dict(), "vm": vm_name})

    def notify_usb_detached(self, usb_info: USBInfo, vm_name: str) -> None:
        self.notify(
            {
                "event": "usb_detached",
                "usb_device": {"device_node": usb_info.device_node},
                "vm": vm_name,
            }
        )

    def notify_pci_attached(self, pci_info: PCIInfo, vm_name: str) -> None:
        self.notify({"event": "pci_attached", "pci_device": pci_info.to_dict(), "vm": vm_name})

    def notify_pci_detached(self, pci_info: PCIInfo, vm_name: str) -> None:
        self.notify({"event": "pci_detached", "pci_device": pci_info.to_dict(), "vm": vm_name})

    def notify_usb_select_vm(self, usb_info: USBInfo, allowed_vms: list[str] | None) -> None:
        self.notify(
            {
                "event": "usb_select_vm",
                "usb_device": usb_info.to_dict(),
                "allowed_vms": allowed_vms,
            }
        )

    def notify_usb_connected(self, usb_info: USBInfo) -> None:
        self.notify({"event": "usb_connected", "usb_device": usb_info.to_dict()})

    def notify_usb_disconnected(self, usb_info: USBInfo) -> None:
        self.notify(
            {
                "event": "usb_disconnected",
                "usb_device": {"device_node": usb_info.device_node},
            }
        )

    def notify_pci_connected(self, pci_info: PCIInfo) -> None:
        self.notify({"event": "pci_connected", "pci_device": pci_info.to_dict()})

    def notify_pci_disconnected(self, pci_info: PCIInfo) -> None:
        self.notify({"event": "pci_disconnected", "pci_device": {"address": pci_info.address}})

    def notify_dev_attached(self, dev_info: USBInfo | PCIInfo, vm_name: str) -> None:
        if isinstance(dev_info, USBInfo):
            self.notify_usb_attached(dev_info, vm_name)
        else:
            self.notify_pci_attached(dev_info, vm_name)

    def notify_dev_detached(self, dev_info: USBInfo | PCIInfo, vm_name: str) -> None:
        if isinstance(dev_info, USBInfo):
            self.notify_usb_detached(dev_info, vm_name)
        else:
            self.notify_pci_detached(dev_info, vm_name)

    def notify_dev_connected(self, dev_info: USBInfo | PCIInfo) -> None:
        if isinstance(dev_info, USBInfo):
            self.notify_usb_connected(dev_info)
        else:
            self.notify_pci_connected(dev_info)

    def notify_dev_disconnected(self, dev_info: USBInfo | PCIInfo) -> None:
        if isinstance(dev_info, USBInfo):
            self.notify_usb_disconnected(dev_info)
        else:
            self.notify_pci_disconnected(dev_info)

    def handle_message(self, client_sock: socket.socket, client_addr: Any, msg: dict[str, Any]) -> dict[str, Any]:
        action = msg.get("action")
        if not action:
            return {"result": "failed", "error": "No action specified"}
        handler = self.handlers.get(action)
        if handler is None:
            logger.warning("Unknown API request %s from %s", action, client_addr)
            return {"result": "failed", "error": f"Unknown message: {action}"}
        try:
            logger.info('API request "%s" from %s', action, client_addr)
            return handler(client_sock, client_addr, msg)
        except (RuntimeError, TypeError, ValueError) as e:
            logger.exception("Failed to process API request")
            return {"result": "failed", "error": str(e)}

    def _on_enable_notifications(
        self, client_sock: socket.socket, _client_addr: Any, _msg: dict[str, Any]
    ) -> dict[str, str]:
        with self.clients_lock:
            if client_sock not in self.notify_clients:
                self.notify_clients.append(client_sock)
        return {"result": "ok"}

    def _on_usb_list(self, _client_sock: socket.socket, _client_addr: Any, _msg: dict[str, Any]) -> dict[str, Any]:
        return {"result": "ok", "usb_devices": get_usb_devices(self.app_context)}

    def _on_usb_attach(self, _client_sock: socket.socket, _client_addr: Any, msg: dict[str, Any]) -> dict[str, str]:
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
            assert vid is not None and pid is not None, "vid and pid must be set"
            asyncio.run_coroutine_threadsafe(
                attach_existing_usb_device_by_vid_pid(self.app_context, vid, pid, selected_vm),
                self.loop,
            ).result()

        return {"result": "ok"}

    def _on_usb_detach(self, _client_sock: socket.socket, _client_addr: Any, msg: dict[str, Any]) -> dict[str, str]:
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
            assert vid is not None and pid is not None, "vid and pid must be set"
            asyncio.run_coroutine_threadsafe(
                remove_existing_usb_device_by_vid_pid(self.app_context, vid, pid, True),
                self.loop,
            ).result()

        return {"result": "ok"}

    def _on_usb_suspend(self, _client_sock: socket.socket, _client_addr: Any, msg: dict[str, Any]) -> dict[str, str]:
        vm = msg.get("vm")
        asyncio.run_coroutine_threadsafe(
            detach_connected_usb(self.app_context, [vm] if vm else None), self.loop
        ).result()
        return {"result": "ok"}

    def _on_usb_resume(self, _client_sock: socket.socket, _client_addr: Any, msg: dict[str, Any]) -> dict[str, str]:
        vm = msg.get("vm")
        asyncio.run_coroutine_threadsafe(
            attach_connected_usb(self.app_context, [vm] if vm else None), self.loop
        ).result()
        return {"result": "ok"}

    def _on_pci_list(self, _client_sock: socket.socket, _client_addr: Any, _msg: dict[str, Any]) -> dict[str, Any]:
        return {"result": "ok", "pci_devices": get_pci_devices(self.app_context)}

    def _on_pci_attach(self, _client_sock: socket.socket, _client_addr: Any, msg: dict[str, Any]) -> dict[str, str]:
        address = msg.get("address")
        vid = msg.get("vid")
        did = msg.get("did")
        selected_vm = msg.get("vm")
        if address:
            logger.info("Request to attach PCI device %s to %s", address, selected_vm)
            asyncio.run_coroutine_threadsafe(
                attach_existing_pci_device(self.app_context, address, selected_vm),
                self.loop,
            ).result()
        else:
            logger.info(
                "Request to attach PCI device by vid %s and did %s to %s",
                vid,
                did,
                selected_vm,
            )
            assert vid is not None and did is not None, "vid and did must be set"
            asyncio.run_coroutine_threadsafe(
                attach_existing_pci_device_by_vid_did(self.app_context, vid, did, selected_vm),
                self.loop,
            ).result()

        return {"result": "ok"}

    def _on_pci_detach(self, _client_sock: socket.socket, _client_addr: Any, msg: dict[str, Any]) -> dict[str, str]:
        address = msg.get("address")
        vid = msg.get("vid")
        did = msg.get("did")
        if address:
            logger.info("Request to detach PCI device %s", address)
            asyncio.run_coroutine_threadsafe(
                remove_existing_pci_device(self.app_context, address, True),
                self.loop,
            ).result()
        else:
            logger.info("Request to detach PCI device by vid %s and did %s", vid, did)
            assert vid is not None and did is not None, "vid and did must be set"
            asyncio.run_coroutine_threadsafe(
                remove_existing_pci_device_by_vid_did(self.app_context, vid, did, True),
                self.loop,
            ).result()

        return {"result": "ok"}

    def _on_pci_suspend(self, _client_sock: socket.socket, _client_addr: Any, msg: dict[str, Any]) -> dict[str, str]:
        vm = msg.get("vm")
        asyncio.run_coroutine_threadsafe(
            detach_connected_pci(self.app_context, [vm] if vm else None), self.loop
        ).result()
        return {"result": "ok"}

    def _on_pci_resume(self, _client_sock: socket.socket, _client_addr: Any, msg: dict[str, Any]) -> dict[str, str]:
        vm = msg.get("vm")
        asyncio.run_coroutine_threadsafe(
            attach_connected_pci(self.app_context, [vm] if vm else None), self.loop
        ).result()
        return {"result": "ok"}

    def _on_disconnected_list(
        self, _client_sock: socket.socket, _client_addr: Any, _msg: dict[str, Any]
    ) -> dict[str, Any]:
        return {"result": "ok", "disconnected_devices": self.app_context.dev_state.list_disconnected()}

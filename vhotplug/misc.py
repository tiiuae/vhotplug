import logging
import os
import socket
import time
from socket import SocketKind

logger = logging.getLogger("vhotplug")


def is_unix_socket_alive(socket_path: str, sock_type: SocketKind) -> bool:
    """Tests if unix socket is alive by trying to connect to it."""
    if not os.path.exists(socket_path):
        return False
    try:
        client = socket.socket(socket.AF_UNIX, sock_type)
        client.connect(socket_path)
        client.close()
        return True
    except OSError as e:
        logger.warning("Socket %s is not alive: %s", socket_path, e)
    return False


def wait_for_unix_socket(
    socket_path: str,
    vm_boot_timeout: int,
    wait_after_boot: int,
    sock_type: SocketKind,
    poll_interval: float = 1,
) -> bool:
    """Waits for a unix socket to become available."""
    if poll_interval <= 0:
        raise ValueError("Socket poll interval must be greater than zero")
    if not os.path.exists(socket_path):
        return False

    deadline = time.monotonic() + vm_boot_timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if is_unix_socket_alive(socket_path, sock_type):
            stat = os.stat(socket_path)
            uptime = time.time() - stat.st_ctime
            logger.debug("Socket %s uptime: %s seconds, attempt %s", socket_path, int(uptime), attempt)
            if uptime >= wait_after_boot:
                return True
        else:
            logger.debug("Socket %s is not alive", socket_path)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))
    return False

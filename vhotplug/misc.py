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


def wait_for_unix_socket(socket_path: str, vm_boot_timeout: int, wait_after_boot: int, sock_type: SocketKind) -> bool:
    """Waits for a unix socket to become avaiable."""
    for attempt in range(1, vm_boot_timeout + 1):
        if is_unix_socket_alive(socket_path, sock_type):
            stat = os.stat(socket_path)
            uptime = time.time() - stat.st_ctime
            logger.debug("Socket %s uptime: %s seconds, attempt %s", socket_path, int(uptime), attempt)
            if uptime >= wait_after_boot:
                return True
        else:
            logger.warning("Socket %s is not alive", socket_path)
        time.sleep(1)
    return False

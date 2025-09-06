import logging
import os
import socket
import time

logger = logging.getLogger("vhotplug")

def is_unix_socket_alive(socket_path, sock_type):
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

def wait_for_boot(socket_path, vm_boot_timeout, wait_after_boot, sock_type):
    for attempt in range(1, vm_boot_timeout + 1):
        if is_unix_socket_alive(socket_path, sock_type):
            stat = os.stat(socket_path)
            uptime = time.time() - stat.st_ctime
            logger.debug("VM uptime: %s seconds, attempt %s", int(uptime), attempt)
            if uptime >= wait_after_boot:
                return True
        else:
            logger.warning("VM is not running: %s", socket_path)
        time.sleep(1)
    return False

def wait_for_boot_crosvm(socket_path, vm_boot_timeout, wait_after_boot):
    return wait_for_boot(socket_path, vm_boot_timeout, wait_after_boot, socket.SOCK_SEQPACKET)

def wait_for_boot_qemu(socket_path, vm_boot_timeout, wait_after_boot):
    return wait_for_boot(socket_path, vm_boot_timeout, wait_after_boot, socket.SOCK_STREAM)

from qemu.qmp import QMPClient
import logging
import asyncio

logger = logging.getLogger("vhotplug")

class QEMULink:
    retry_count = 5
    retry_timeout = 1

    def __init__(self, socket_path):
        self.socket_path = socket_path

    async def query_commands(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-commands")
            logger.info("QMP Commands:")
            for x in res:
                logger.info(x)
        except Exception as e:
            logger.error(f"Failed to get a list of commands: {e}")
        finally:
            await qmp.disconnect()

    async def usb(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("human-monitor-command", {"command-line": "info usb"})
            logger.info(f"USB Guest Devices:")
            logger.info(res)
        except Exception as e:
            logger.error(f"Failed to get a list of USB guest devices: {e}")
        finally:
            await qmp.disconnect()

    async def usbhost(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("human-monitor-command", {"command-line": "info usbhost"})
            logger.info(f"USB Host Devices:")
            logger.info(res)
        except Exception as e:
            logger.error(f"Failed to get a list of USB host devices: {e}")
        finally:
            await qmp.disconnect()

    async def query_status(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-status")
            return res['status']
        except:
            pass
        finally:
            await qmp.disconnect()

    async def query_pci(self):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("query-pci")
            logger.info("PCI:")
            for x in res:
                logger.info(x['bus'])
                for dev in x['devices']:
                    logger.info(dev)
        except Exception as e:
            logger.error(f"Failed to query PCI: {e}")
        finally:
            await qmp.disconnect()

    async def add_usb_device(self, device):
        i = 0
        while True:
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                busnum = int(device.properties["BUSNUM"])
                devnum = int(device.properties["DEVNUM"])
                qemuid = f"usb{busnum}{devnum}"
                logger.info(f"Adding USB device with id {qemuid}")
                res = await qmp.execute("device_add", {"driver": "usb-host", "hostbus": busnum, "hostaddr": devnum, "id": qemuid})
                if res:
                    logger.error(f"Failed to add device: {res}")
                else:
                    logger.info(f"Attached USB device. BUSNUM: {busnum}, DEVNUM: {devnum}.")
                return
            except Exception as e:
                if str(e).startswith("Duplicate device ID"):
                    logger.info(f"USB device {qemuid} is already attached to the VM")
                    return
                else:
                    logger.error(f"Failed to add USB device: {e}")
                    i += 1
            finally:
                await qmp.disconnect()

            if i < self.retry_count:
                logger.info(f"Retrying")
                await asyncio.sleep(self.retry_timeout)
            else:
                break
        logger.error(f"Failed to add USB device {qemuid}")

    async def remove_usb_device(self, device):
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            busnum = int(device.properties["BUSNUM"])
            devnum = int(device.properties["DEVNUM"])
            qemuid = f"usb{busnum}{devnum}"
            res = await qmp.execute("device_del", {"id": qemuid})
            if res:
                logger.error(f"Failed to remove USB device: {res}")
            else:
                logger.info(f"Removed USB device. BUSNUM: {busnum}, DEVNUM: {devnum}.")
        except Exception as e:
            logger.error(f"Failed to remove USB device: {e}")
        finally:
            await qmp.disconnect()

    async def add_evdev_device(self, device, bus):
        idindex = 0
        i = 0
        while True:
            qemuid = device.sys_name
            if idindex > 0:
                qemuid += f"-{idindex}"
            logger.info(f"Adding evdev device {device.device_node} with id {qemuid}")
            qmp = QMPClient()
            try:
                await qmp.connect(self.socket_path)
                res = await qmp.execute("device_add", {"driver": "virtio-input-host-pci", "evdev": device.device_node, "id": qemuid, "bus": bus})
                if res:
                    logger.error(f"Failed to add evdev device: {res}")
                else:
                    logger.info(f"Attached evdev device {device.device_node}")
                    return
            except Exception as e:
                logger.error(f"Failed to add evdev device: {e}")
                if str(e).startswith("Duplicate device ID"):
                    idindex += 1
                else:
                    logger.error(f"{type(e)}")
                    logger.error(f"{e.args}")
                    i += 1
            finally:
                await qmp.disconnect()

            if i < self.retry_count:
                logger.info(f"Retrying")
                await asyncio.sleep(self.retry_timeout)
            else:
                break
        logger.error(f"Failed to add evdev device {device.device_node}")

    async def remove_evdev_device(self, device):
        logger.info(f"Removing evdev device {device.device_node} with id {device.sys_name}")
        qmp = QMPClient()
        try:
            await qmp.connect(self.socket_path)
            res = await qmp.execute("device_del", {"id": device.sys_name})
            if res:
                logger.error(f"Failed to remove evdev device: {res}")
            else:
                logger.info(f"Removed evdev device {device.sys_name}")
        except Exception as e:
            logger.error(f"Failed to remove evdev device: {e}")
        finally:
            await qmp.disconnect()

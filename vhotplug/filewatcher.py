import os
from inotify_simple import INotify, flags
import logging

logger = logging.getLogger("vhotplug")

class FileWatcher:
    def __init__(self):
        self.inotify = INotify()
        self.watch_descriptors = {}

    def directory_monitored(self, directory_name):
        return any(desc['directory'] == directory_name for desc in self.watch_descriptors.values())

    def get_directory_wd(self, directory_name):
        for wd, desc in self.watch_descriptors.items():
            if desc['directory'] == directory_name:
                return wd
        return None

    def add_file(self, file_path):
        directory = os.path.dirname(file_path)
        filename = os.path.basename(file_path)
        logger.info(f"Watching for {filename} in {directory}")

        if not self.directory_monitored(directory):
            watch_flags = flags.CREATE | flags.DELETE
            wd = self.inotify.add_watch(directory, watch_flags)
            self.watch_descriptors[wd] = {
                'directory': directory,
                'files': set()
            }

        wd = self.get_directory_wd(directory)
        if wd == None:
            logger.error(f"Directory {directory} is not being monitored")
        else:
            self.watch_descriptors[wd]['files'].add(filename)

    def detect_restart(self):
        vm_restart_detected = False
        try:
            events = self.inotify.read(timeout=0)
            for event in events:
                #logger.debug(event)
                directory = self.watch_descriptors[event.wd]['directory']
                filename = event.name
                if filename in self.watch_descriptors[event.wd]['files']:
                    file_path = os.path.join(directory, filename)
                    if event.mask & flags.CREATE:
                        logger.info(f"VM {file_path} started")
                        vm_restart_detected = True
                    if event.mask & flags.DELETE:
                        logger.info(f"VM {file_path} stopped")
        except BlockingIOError:
            pass
        return vm_restart_detected

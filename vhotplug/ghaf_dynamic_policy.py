import subprocess
import logging
import socket
import time
import json
import os

logger = logging.getLogger("vhotplug")

class GhafDynamicPolicy:
    def __init__(self, admin_name, admin_addr, admin_port, policy_query, givc_cli, cert = None, key = None, cacert = None):

        self.policy_json = None
        self.admin_name = admin_name
        self.admin_addr = admin_addr
        self.admin_port = str(admin_port)
        if cert is not None:
            if not os.path.exists(cert):
                raise FileNotFoundError(f"File {cert} does not exist.")
            if not os.path.exists(key):
                raise FileNotFoundError(f"File {key} does not exist.")
            if not os.path.exists(cacert):
                raise FileNotFoundError(f"File {cacert} does not exist.")
            if not os.path.exists(givc_cli):
                raise FileNotFoundError(f"File {givc_cli} does not exist.")
            self.policy_query_cmd = [
                givc_cli,
                "--cert", cert,
                "--key", key,
                "--cacert", cacert,
                "--name", self.admin_name,
                "--addr", self.admin_addr,
                "--port", self.admin_port,
                "policy-query", f"{policy_query}"
            ]
        else:
            self.policy_query_cmd = [
                givc_cli,
                "--notls",
                "--name", self.admin_name,
                "--addr", self.admin_addr,
                "--port", self.admin_port,
                "policy-query", f"{policy_query}"
            ]


    def __remove_comments(self, json_as_string):
        result = ""
        for line in json_as_string.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            # Remove inline comment
            code_part = line.split('#', 1)[0].rstrip()
            if code_part:
                result += code_part + "\n"
        return result

    def __wait_for_admin(self, timeout=60, interval=2):
        logger.info("Waiting for admin vm to become reachable...")

        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                with socket.create_connection((self.admin_addr, self.admin_port), timeout=3):
                    logger.info(f"Admin vm [{self.admin_addr}:{self.admin_port}] is reachable.")
                    return True
            except (socket.timeout, ConnectionRefusedError, OSError):
                logger.info(f"Admin vm [{self.admin_addr}:{self.admin_port}] is still not reachable.")
                time.sleep(interval)

        logger.error(f"Admin vm [{self.admin_addr}:{self.admin_port}] is not reachable. Timed out after {timeout} seconds!")
        return False

    def __fetch_hotplug_policy(self):
        if self.__wait_for_admin() == None:
            return None
        try:
            result = subprocess.run(
                self.policy_query_cmd,
                capture_output=True,
                text=True,
                check=True,
                encoding='utf-8'
            )
            output_string = result.stdout.strip()
            logger.debug(f"Raw USB Hotplug Policy received:\n{output_string}")

            if not output_string:
                logger.error("Error: Policy fetcher command returned empty output.")
                return None

            try:
                outer = json.loads(output_string)
                inner = None
                if isinstance(outer, str):
                    inner = json.loads(outer)
                else:
                    inner = outer

                if isinstance(inner, dict) and "result" in inner:
                    self.policy_json = inner["result"]
                else:
                    logger.error("Policy fetcher command returned unexpected output.")
                    return None
                return self.policy_json

            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode JSON from command output. JSONDecodeError: {e}")
                logger.error(f"Raw output was:\n---\n{output_string}\n---")
                return None

        except subprocess.CalledProcessError as e:
            logger.error(f"Command execution failed with exit code {e.returncode}.")
            logger.error(f"Stderr:\n---\n{result.stderr}\n---")
            return None

    def get_policy(self):
        if self.policy_json == None:
            return self.__fetch_hotplug_policy()
        else:
            return self.policy_json

    def reload_policy(self):
        self.policy_json = None

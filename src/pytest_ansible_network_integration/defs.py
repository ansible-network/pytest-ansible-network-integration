"""Common objects."""
import logging
import os
import re
import subprocess
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

import xmltodict

# pylint: disable=no-name-in-module
from pylibsshext.errors import LibsshSessionException
from pylibsshext.session import Channel
from pylibsshext.session import Session


# pylint: enable=no-name-in-module
logger = logging.getLogger(__name__)


def worker_log(message: str, level: int) -> None:
    """Log message with worker id.

    :param message: Message to log.
    :param level: Logging level.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    logger.log(level, "[%s] %s", worker_id, message)


@dataclass
class AnsibleProject:
    """Ansible project."""

    directory: Path
    inventory: Path
    log_file: Path
    playbook_artifact: Path
    playbook: Path
    role: str


class SshWrapper:
    """Wrapper for pylibssh."""

    def __init__(self, host: str, user: str, password: str, port: int = 22):
        """Initialize the wrapper.

        :param host: The host
        :param user: The user
        :param password: The password
        :param port: The port
        """
        self.host = host
        self.password = password
        self.port = port
        self.session = Session()
        self.ssh_channel: Channel
        self.user = user

    def connect(self) -> None:
        """Connect to the host.

        :raises LibsshSessionException: If the connection fails
        """
        try:
            worker_log(message=f"Connecting to {self.host}", level=logging.INFO)
            self.session.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                host_key_checking=False,
                look_for_keys=False,
            )
        except LibsshSessionException as exc:
            raise exc
        self.ssh_channel = self.session.new_channel()

    def execute(self, command: str) -> Tuple[str, str]:
        """Execute the command.

        :param command: The command
        :raises LibsshSessionException: If the channel fails
        :return: The result
        """
        if not self.session.is_connected:
            self.close()
            self.connect()
        try:
            result = self.ssh_channel.exec_command(command)
            stdout = result.stdout.decode()
            stderr = result.stderr.decode()
            return stdout, stderr
        except LibsshSessionException as exc:
            raise exc

    def close(self) -> None:
        """Close the channel."""
        self.ssh_channel.close()


class CmlWrapper:
    """Wrapper for cml."""

    def __init__(self, host: str, username: str, password: str) -> None:
        """Initialize the wrapper.

        :param host: The host
        :param username: The username
        :param password: The password
        """
        self.current_lab_id: str
        self._host = host
        self._auth_env = {
            "VIRL_HOST": host,
            "VIRL_USERNAME": username,
            "VIRL_PASSWORD": password,
            "CML_VERIFY_CERT": "False",
        }
        self._lab_existed: bool = False

    def bring_up(self, file: str) -> None:
        """Bring the lab up.

        :param file: The file
        :raises Exception: If the lab fails to start
        """
        worker_log(message="Check if lab is already provisioned", level=logging.INFO)
        stdout, _stderr = self._run("id")
        if stdout:
            current_lab_match = re.match(r".*ID: (?P<id>\S+)\)\n", stdout, re.DOTALL)
            if current_lab_match:
                self.current_lab_id = current_lab_match.groupdict()["id"]
                worker_log(
                    message=f"Using existing lab id '{self.current_lab_id}'", level=logging.INFO
                )
                self._lab_existed = True
                return
        worker_log(message="No lab currently provisioned", level=logging.INFO)
        worker_log(message=f"Bringing up lab '{file}' on '{self._host}'", level=logging.INFO)
        # Using --provision was not reliable
        stdout, stderr = self._run(f"up -f {file}")
        worker_log(message=f"CML up stdout: '{stdout}'", level=logging.DEBUG)
        # Starting lab xxx (ID: 9fde5f)\n
        current_lab_match = re.match(r".*ID: (?P<id>\S+)\)\n", stdout, re.DOTALL)
        if not current_lab_match:
            raise Exception(f"Could not get lab ID: {stdout} {stderr}")
        self.current_lab_id = current_lab_match.groupdict()["id"]
        worker_log(message=f"Started lab id '{self.current_lab_id}'", level=logging.INFO)

    def remove(self) -> None:
        """Remove the lab."""
        if self._lab_existed:
            worker_log(
                message=f"Please remember to remove lab id '{self.current_lab_id}'",
                level=logging.INFO,
            )
            return

        worker_log(
            message=f"Deleting lab '{self.current_lab_id}' on '{self._host}'", level=logging.INFO
        )
        stdout, _stderr = self._run(f"use --id {self.current_lab_id}")
        worker_log(message=f"CML use stdout: '{stdout}'", level=logging.DEBUG)
        stdout, _stderr = self._run("rm --force --no-confirm")
        worker_log(message=f"CML rm stdout: '{stdout}'", level=logging.DEBUG)

    def _run(self, command: str) -> Tuple[str, str]:
        """Run the command.

        :param command: The command
        :return: The result, stdout and stderr
        """
        cml_command = f"cml {command}"
        worker_log(message=f"Running command '{cml_command}' on '{self._host}'", level=logging.INFO)
        env = os.environ.copy()
        if "VIRTUAL_ENV" in os.environ:
            env["PATH"] = os.path.join(os.environ["VIRTUAL_ENV"], "bin") + os.pathsep + env["PATH"]

        env.update(self._auth_env)

        worker_log(
            message=f"Running command '{cml_command}' with environment '{env}'", level=logging.DEBUG
        )
        with subprocess.Popen(
            cml_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        ) as process:
            stdout, stderr = process.communicate()
        return stdout.decode(), stderr.decode()


class VirshWrapper:
    """Wrapper for virsh."""

    def __init__(self, host: str, user: str, password: str, port: int) -> None:
        """Initialize the wrapper.

        :param host: The host
        :param user: The user
        :param password: The password
        :param port: The port
        """
        self.ssh = SshWrapper(host=host, user=user, password=password, port=port)
        self.ssh.connect()

    def get_dhcp_lease(self, current_lab_id: str) -> str:
        """Get the dhcp lease.

        :param current_lab_id: The current lab id
        :raises Exception: If the dhcp lease cannot be found
        :return: The ip address
        """
        attempt = 0
        current_lab: Dict[str, Any] = {}

        worker_log(message="Getting current lab from virsh", level=logging.INFO)

        while not current_lab:
            worker_log(f"Get DHCP lease attempt: {attempt}", level=logging.INFO)
            stdout, _stderr = self.ssh.execute("sudo virsh list --all")

            virsh_matches = [re.match(r"^\s(?P<id>\d+)", line) for line in stdout.splitlines()]
            virsh_ids = [
                virsh_match.groupdict()["id"] for virsh_match in virsh_matches if virsh_match
            ]

            for virsh_id in virsh_ids:
                stdout, _stderr = self.ssh.execute(f"sudo virsh dumpxml {virsh_id}")
                if current_lab_id in stdout:
                    worker_log(
                        message=f"Found lab '{current_lab_id}' in virsh dumpxml: {stdout}",
                        level=logging.DEBUG,
                    )
                    current_lab = xmltodict.parse(stdout)
                    break
            if current_lab:
                break
            attempt += 1
            if attempt == 10:
                raise Exception("Could not find current lab")
            time.sleep(5)

        macs = [
            interface["mac"]["@address"]
            for interface in current_lab["domain"]["devices"]["interface"]
        ]
        worker_log(message=f"Found macs: {macs}", level=logging.INFO)

        worker_log(message=f"Getting a DHCP lease for any of {macs}", level=logging.INFO)
        ips: List[str] = []
        attempt = 0
        while not ips:
            worker_log(message=f"Get DHCP lease attempt: {attempt}", level=logging.INFO)
            stdout, _stderr = self.ssh.execute("sudo virsh net-dhcp-leases default")
            leases = {
                p[2]: p[4].split("/")[0]
                for p in [line.split() for line in stdout.splitlines()]
                if len(p) == 7
            }

            ips = [leases[mac] for mac in macs if mac in leases]
            attempt += 1
            if attempt == 30:
                raise Exception("Could not find IPs")
            time.sleep(10)

        worker_log(message=f"Found IPs: {ips}", level=logging.DEBUG)

        if len(ips) > 1:
            raise Exception("Found more than one IP")

        return ips[0]

    def close(self) -> None:
        """Close the connection."""
        self.ssh.close()

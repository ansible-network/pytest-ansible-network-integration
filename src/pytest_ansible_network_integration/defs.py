"""Common objects."""

from __future__ import annotations

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
from typing import Optional
from typing import Tuple

import xmltodict

# pylint: disable=no-name-in-module
from pylibsshext.errors import LibsshSessionException
from pylibsshext.session import Channel
from pylibsshext.session import Session


# pylint: enable=no-name-in-module

logger = logging.getLogger(__name__)


@dataclass
class AnsibleProject:
    """Ansible project."""

    collection_doc_cache: Path
    directory: Path
    log_file: Path
    playbook_artifact: Path
    playbook: Path
    role: str
    inventory: Optional[Path] = None


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
            logger.debug("Connecting to %s", self.host)
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
        :raises PytestNetworkError: If the lab fails to start
        """
        logger.info("Check if lab is already provisioned")
        stdout, _stderr = self._run("id")
        if stdout:
            current_lab_match = re.match(r".*ID: (?P<id>\S+)\)\n", stdout, re.DOTALL)
            if current_lab_match:
                self.current_lab_id = current_lab_match.groupdict()["id"]
                logger.info("Using existing lab id '%s'", self.current_lab_id)
                self._lab_existed = True
                return
        logger.info("No lab currently provisioned")
        logger.info("Bringing up lab '%s' on '%s'", file, self._host)
        # Using --provision was not reliable
        stdout, stderr = self._run(f"up -f {file}")
        logger.info("CML up stdout: '%s'", stdout)
        # Starting lab xxx (ID: 9fde5f)\n
        current_lab_match = re.match(r".*ID: (?P<id>\S+)\)", stdout, re.DOTALL)
        if not current_lab_match:
            raise PytestNetworkError(f"Could not get lab ID: {stdout} {stderr}")
        self.current_lab_id = current_lab_match.groupdict()["id"]
        logger.info("Started lab id '%s'", self.current_lab_id)

        if not os.environ.get("GITHUB_ACTIONS"):
            return
        # In the case of GH actions store the labs in an env var for clean up if the job is
        # cancelled, this is referenced in the GH integration workflow

        env_file = os.environ.get("GITHUB_ENV", "")
        if not env_file:
            return
        with open(env_file, "r", encoding="utf-8") as fh:
            data = fh.readlines()

        line_id = [idx for idx, line in enumerate(data) if line.startswith("CML_LABS=")]
        if not line_id:
            data.append(f"CML_LABS={self.current_lab_id}")
        else:
            data[line_id[0]] += f",{self.current_lab_id}"

        with open(env_file, "w", encoding="utf-8") as fh:
            fh.writelines(data)

    def remove(self) -> None:
        """Remove the lab."""
        if self._lab_existed:
            logger.info("Please remember to remove lab id '%s'", self.current_lab_id)
            return

        logger.info("Deleting lab '%s' on '%s'", self.current_lab_id, self._host)
        stdout, _stderr = self._run(f"use --id {self.current_lab_id}")
        logger.debug("CML use stdout: '%s'", stdout)
        stdout, _stderr = self._run("rm --force --no-confirm")
        logger.debug("CML rm stdout: '%s'", stdout)

    def _run(self, command: str) -> Tuple[str, str]:
        """Run the command.

        :param command: The command
        :return: The result, stdout and stderr
        """
        cml_command = f"cml {command}"
        logger.info("Running command '%s' on '%s'", cml_command, self._host)
        env = os.environ.copy()
        if "VIRTUAL_ENV" in os.environ:
            env["PATH"] = os.path.join(os.environ["VIRTUAL_ENV"], "bin") + os.pathsep + env["PATH"]

        env.update(self._auth_env)

        logger.debug("Running command '%s' with environment '%s'", cml_command, env)
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
        :raises PytestNetworkError: If the dhcp lease cannot be found
        :return: The ip address
        """
        attempt = 0
        current_lab: Dict[str, Any] = {}

        logger.info("Getting current lab from virsh")
        while not current_lab:
            logger.info("Attempt %s", attempt)
            stdout, _stderr = self.ssh.execute("sudo virsh list --all")

            virsh_matches = [re.match(r"^\s(?P<id>\d+)", line) for line in stdout.splitlines()]
            virsh_ids = [
                virsh_match.groupdict()["id"] for virsh_match in virsh_matches if virsh_match
            ]

            for virsh_id in virsh_ids:
                stdout, _stderr = self.ssh.execute(f"sudo virsh dumpxml {virsh_id}")
                if current_lab_id in stdout:
                    logger.debug("Found lab %s in virsh dumpxml: %s", current_lab_id, stdout)
                    current_lab = xmltodict.parse(stdout)
                    break
            if current_lab:
                break
            attempt += 1
            if attempt == 10:
                raise PytestNetworkError("Could not find current lab")
            time.sleep(5)

        macs = [
            interface["mac"]["@address"]
            for interface in current_lab["domain"]["devices"]["interface"]
        ]
        logger.info("Found macs: %s", macs)

        logger.info("Getting a DHCP lease for any of %s", macs)
        ips: List[str] = []
        attempt = 0
        while not ips:
            logger.info("Attempt %s", attempt)
            stdout, _stderr = self.ssh.execute("sudo virsh net-dhcp-leases default")
            leases = {
                p[2]: p[4].split("/")[0]
                for p in [line.split() for line in stdout.splitlines()]
                if len(p) == 7
            }

            ips = [leases[mac] for mac in macs if mac in leases]
            attempt += 1
            if attempt == 50:
                raise PytestNetworkError("Could not find IPs")
            time.sleep(10)

        logger.debug("Found IPs: %s", ips)

        if len(ips) > 1:
            raise PytestNetworkError("Found more than one IP")

        return ips[0]

    def close(self) -> None:
        """Close the connection."""
        self.ssh.close()


class PytestNetworkError(Exception):
    """Class representing exceptions raised from the pytest plugin code."""

    def __init__(self: PytestNetworkError, message: str) -> None:
        """Instantiate an object of this class.

        :param message: The exception message.
        """
        super().__init__(message)

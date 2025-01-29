# cspell:ignore cmlutils
"""This module contains Common objects for Ansible network integration tests plugin."""

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

from .exceptions import PytestNetworkError


# pylint: enable=no-name-in-module

logger = logging.getLogger(__name__)


@dataclass
class AnsibleProject:
    """Creates an Ansible project."""

    collection_doc_cache: Path
    directory: Path
    log_file: Path
    playbook_artifact: Path
    playbook: Path
    role: str
    inventory: Optional[Path] = None


class SshWrapper:
    """Wrapper for pylibssh to manage SSH connections and execute commands in remote devices."""

    def __init__(self, host: str, user: str, password: str, port: int = 22):
        """Initialize the SSH wrapper.

        :param host: The hostname or IP address of the SSH server.
        :param user: The username to authenticate with.
        :param password: The password to authenticate with.
        :param port: The port number to connect to (default is 22).
        """
        self.host = host
        self.password = password
        self.port = port
        self.session = Session()
        self.ssh_channel: Channel
        self.user = user

    def connect(self) -> None:
        """Connect to the SSH server.

        Establishes an SSH connection to the specified host using the provided
        credentials.

        :raises LibsshSessionException: If the connection fails.
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
            logger.error("Failed to connect to %s", self.host)
            raise exc
        self.ssh_channel = self.session.new_channel()

    def execute(self, command: str) -> Tuple[str, str]:
        """Execute a command on the SSH server.

        Executes the specified command on the connected SSH server and returns
        the standard output and standard error.

        :param command: The command to execute.
        :raises LibsshSessionException: If the channel fails.
        :return: A tuple containing the standard output and standard error.
        """
        if not self.session.is_connected:
            logger.warning("Session is not connected. Reconnecting...")
            self.close()
            self.connect()
        try:
            logger.debug("Executing command: %s", command)
            result = self.ssh_channel.exec_command(command)
            stdout = result.stdout.decode()
            stderr = result.stderr.decode()
            return stdout, stderr
        except LibsshSessionException as exc:
            logger.error("Failed to execute command: %s", command)
            raise exc

    def close(self) -> None:
        """Close the SSH channel.

        Closes the SSH channel to the server.
        """
        logger.debug("Closing SSH channel")
        self.ssh_channel.close()


class CmlWrapper:
    """Wrapper for interacting with CML.

    This class essentially interacts with the library cmlutils.
    """

    def __init__(self, host: str, username: str, password: str) -> None:
        """
        Initialize the CmlWrapper.

        :param host: The CML host.
        :param username: The username for authentication.
        :param password: The password for authentication.
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
        """
        Bring the lab up.

        Checks if the lab is already provisioned, if not, it brings it up using the specified file.
        Uses the `cml up` command to bring up the lab.

        :param file: The file to bring up the lab.
        :raises PytestNetworkError: If the lab fails to start.
        """
        logger.info("Checking if lab is already provisioned")
        stdout, _stderr = self._run("id")
        if stdout:
            current_lab_match = re.match(r".*ID: (?P<id>\S+)\)\n", stdout, re.DOTALL)
            if current_lab_match:
                self.current_lab_id = current_lab_match.groupdict()["id"]
                logger.info("Using existing lab id '%s'", self.current_lab_id)
                self._lab_existed = True
                return
        if _stderr:
            logger.error("CML id stderr: %s", _stderr)

        logger.info("No lab currently provisioned")
        logger.info("Bringing up lab '%s' on '%s'", file, self._host)
        # Using --provision was not reliable, using up instead
        stdout, stderr = self._run(f"up -f {file}")
        logger.debug("CML up stdout: '%s'", stdout)

        # Example output: Starting lab xxx (ID: 9fde5f)\n
        current_lab_match = re.match(r".*ID: (?P<id>\S+)\)\n", stdout, re.DOTALL)
        if not current_lab_match:
            logger.error("Failed to bring up the or match the lab ID")
            logger.error("CML up stderr: %s", stderr)
            raise PytestNetworkError(f"Could not get lab ID: {stdout} {stderr}")

        try:
            self.current_lab_id = current_lab_match.groupdict()["id"]
        except KeyError as e:
            error_message = f"Failed to extract lab ID: {e}"
            logger.error(error_message)
            raise PytestNetworkError(error_message) from e

        logger.info("Started lab id '%s'", self.current_lab_id)

        if os.environ.get("GITHUB_ACTIONS"):
            # In the case of GH actions store the labs in an env var for clean up if the job is
            # cancelled, this is referenced in the GH integration workflow
            self._update_github_env()

    def remove(self) -> None:
        """
        Remove the lab.

        Uses the `cml rm` command to remove the lab.
        """
        if self._lab_existed:
            logger.info("Please remember to remove lab id '%s'", self.current_lab_id)
            return

        logger.info("Deleting lab with ID: '%s' on HOST: '%s'", self.current_lab_id, self._host)
        stdout, _stderr = self._run(f"use --id {self.current_lab_id}")
        logger.debug("CML use command stdout: '%s'", stdout)
        if _stderr:
            logger.error("CML use command stderr: '%s'", _stderr)

        stdout, _stderr = self._run("rm --force --no-confirm")
        logger.debug("CML rm command stdout: '%s'", stdout)
        if _stderr:
            logger.error("CML rm command stderr: '%s'", _stderr)

    def _run(self, command: str) -> Tuple[str, str]:
        """
        Run a command on the CML host.

        :param command: The command to run.
        :return: A tuple containing stdout and stderr.
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

    def _update_github_env(self) -> None:
        """Update the GitHub environment file with the current lab ID."""
        logger.info("Updating GitHub environment file with lab ID")

        env_file = os.environ.get("GITHUB_ENV", "")
        logger.info("GitHub environment file: %s", env_file)
        # Look if below code is running in a GitHub action
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


class VirshWrapper:
    """Wrapper for interacting with virsh via SSH."""

    def __init__(self, host: str, user: str, password: str, port: int) -> None:
        """Initialize the VirshWrapper.

        :param host: The hostname or IP address of the SSH server.
        :param user: The username to authenticate with.
        :param password: The password to authenticate with.
        :param port: The port number to connect to.
        """
        self.ssh = SshWrapper(host=host, user=user, password=password, port=port)
        self.ssh.connect()
        logger.info("Connected to virsh host %s", host)

    def get_dhcp_lease(self, current_lab_id: str) -> str:
        """Get the DHCP lease for the specified lab.

        :param current_lab_id: The current lab ID.
        :raises PytestNetworkError: If the DHCP lease cannot be found.
        :return: The IP address associated with the lab.
        """
         # Wait for 10 minutes before starting to get the IP
        logger.info("Waiting for few mins to starting the lab to get the IP...")
        time.sleep(600)

        logger.info("Getting current lab from virsh")
        current_lab = self._find_current_lab(current_lab_id, 20)

        macs = self._extract_macs(current_lab)
        logger.info("Found MAC addresses: %s", macs)

        ips = self._find_dhcp_lease(macs, 100)
        logger.debug("Found IPs: %s", ips)

        if len(ips) > 1:
            logger.error("Found more than one IP: %s", ips)
            raise PytestNetworkError("Found more than one IP")

        logger.info("DHCP lease IP found: %s", ips[0])
        return ips[0]

    def _find_current_lab(self, current_lab_id: str, max_attempts: int = 20) -> Dict[str, Any]:
        """Find the current lab by its ID.

        :param current_lab_id: The current lab ID.
        :param max_attempts: The maximum number of attempts to find the lab.
        :raises PytestNetworkError: If the current lab cannot be found.
        :return: A dictionary representing the current lab.
        """
        attempt = 0

        while attempt < max_attempts:
            logger.info("Attempt %s to find the current lab", attempt)
            stdout, _stderr = self.ssh.execute("sudo virsh list --all")
            logger.debug("virsh list output: %s", stdout)
            if _stderr:
                logger.error("virsh list stderr: %s", _stderr)

            virsh_matches = [re.match(r"^\s(?P<id>\d+)", line) for line in stdout.splitlines()]
            if not any(virsh_matches):
                logger.error("No matching virsh IDs found in the output")
                raise PytestNetworkError("No matching virsh IDs found")

            try:
                virsh_ids = [
                    virsh_match.groupdict()["id"] for virsh_match in virsh_matches if virsh_match
                ]
            except KeyError as e:
                error_message = f"Failed to extract virsh IDs: {e}"
                logger.error(error_message)
                raise PytestNetworkError(error_message) from e

            for virsh_id in virsh_ids:
                stdout, _stderr = self.ssh.execute(f"sudo virsh dumpxml {virsh_id}")
                if current_lab_id in stdout:
                    logger.debug("Found lab %s in virsh dumpxml: %s", current_lab_id, stdout)
                    xmltodict_data = xmltodict.parse(stdout)
                    return xmltodict_data  # type: ignore

            attempt += 1
            time.sleep(5)

        logger.error("Could not find current lab after %s attempts", attempt)
        raise PytestNetworkError("Could not find current lab")

    def _extract_macs(self, current_lab: Dict[str, Any]) -> List[str]:
        """Extract MAC addresses from the current lab.

        :param current_lab: A dictionary representing the current lab.
        :raises PytestNetworkError: If the MAC addresses cannot be extracted.
        :return: A list of MAC addresses.
        """
        try:
            macs = [
                interface["mac"]["@address"]
                for interface in current_lab["domain"]["devices"]["interface"]
            ]
            return macs
        except KeyError as e:
            error_message = f"Failed to extract MAC addresses: {e}"
            logger.error(error_message)
            raise PytestNetworkError(error_message) from e

    def _find_dhcp_lease(self, macs: List[str], max_attempts: int = 100) -> List[str]:
        """Find the DHCP lease for the given MAC addresses.

        :param macs: A list of MAC addresses.
        :param max_attempts: The maximum number of attempts to find the DHCP lease.
        :raises PytestNetworkError: If the DHCP lease cannot be found.
        :return: A list of IP addresses.
        """
        attempt = 0
        ips: List[str] = []

        while attempt < max_attempts:
            logger.info("Attempt %s to find DHCP lease", attempt)
            stdout, _stderr = self.ssh.execute("sudo virsh net-dhcp-leases default")
            logger.debug("virsh net-dhcp-leases output: %s", stdout)
            if _stderr:
                logger.error("virsh net-dhcp-leases stderr: %s", _stderr)

            try:
                leases = {
                    p[2]: p[4].split("/")[0]
                    for p in [line.split() for line in stdout.splitlines()]
                    if len(p) == 7
                }
            except (IndexError, ValueError) as e:
                error_message = f"Failed to parse DHCP leases: {e}"
                logger.error(error_message)
                raise PytestNetworkError(error_message) from e

            try:
                ips = [leases[mac] for mac in macs if mac in leases]
            except KeyError as e:
                error_message = f"Failed to find IP for MAC address: {e}"
                logger.error(error_message)
                raise PytestNetworkError(error_message) from e

            if ips:
                return ips

            attempt += 1
            time.sleep(10)

        logger.error("Could not find IPs after %s attempts", attempt)
        raise PytestNetworkError("Could not find IPs")

    def close(self) -> None:
        """Close the SSH connection."""
        self.ssh.close()
        logger.info("Closed SSH connection to virsh host")

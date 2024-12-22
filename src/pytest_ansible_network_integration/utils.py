"""Utility functions for the Ansible network integration tests plugin."""

import os

from typing import Any, Dict, List

def _print(message: str) -> None:
    """Print a message and flush.

    This ensures the message doesn't get buffered and mixed in the test stdout.

    :param message: The message
    """
    print(f"{message}", flush=True)

def _github_action_log(message: str) -> None:
    """Log a message to GitHub Actions.

    :param message: The message
    """
    if os.environ.get("GITHUB_ACTIONS"):
        _print(message)

def _inventory(
    host: str,
    httpapi_port: int,
    network_os: str,
    password: str,
    port: int,
    username: str,
) -> Dict[str, Any]:
    # pylint: disable=too-many-arguments
    """Build an ansible inventory.

    :param host: The hostname
    :param httpapi_port: The HTTPAPI port
    :param network_os: The network OS
    :param password: The password
    :param port: The port
    :param username: The username
    :returns: The inventory
    """
    inventory = {
        "all": {
            "hosts": {
                "appliance": {
                    "ansible_become": False,
                    "ansible_host": host,
                    "ansible_user": username,
                    "ansible_password": password,
                    "ansible_port": port,
                    "ansible_httpapi_port": httpapi_port,
                    "ansible_connection": "ansible.netcommon.network_cli",
                    "ansible_network_cli_ssh_type": "libssh",
                    "ansible_python_interpreter": "python",
                    "ansible_network_import_modules": True,
                }
            },
            "vars": {"ansible_network_os": network_os},
        }
    }
    return inventory

def playbook(hosts: str, role: str) -> List[Dict[str, object]]:
    """Return the playbook.

    :param hosts: The hosts entry for the playbook
    :param role: The role's path
    :returns: The playbook
    """
    task = {"name": f"Run role {role}", "include_role": {"name": role}}
    play = {"hosts": hosts, "gather_facts": False, "tasks": [task]}
    playbook_obj = [play]
    return playbook_obj

from typing import Tuple

def calculate_ports(appliance_dhcp_address: str) -> Dict[str, int]:
    """Calculate ports based on the appliance DHCP address.

    :param appliance_dhcp_address: The DHCP address of the appliance.
    :return: A tuple containing the SSH port, HTTPS port, HTTP port, and NETCONF port.
    """
    octets = appliance_dhcp_address.split(".")
    ssh_port = 2000 + int(octets[-1])
    https_port = 4000 + int(octets[-1])
    http_port = 8000 + int(octets[-1])
    netconf_port = 3000 + int(octets[-1])
    return {
        "ssh_port": ssh_port,
        "https_port": https_port,
        "http_port": http_port,
        "netconf_port": netconf_port,
    }
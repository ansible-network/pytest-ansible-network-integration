# cspell:ignore nodeid
"""Common fixtures for tests."""
import json
import logging
import os
import sys
import time

from pathlib import Path
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generator
from typing import List

import pytest

from pluggy._result import _Result as pluggy_result

from .defs import AnsibleProject
from .defs import CmlWrapper
from .defs import VirshWrapper


logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def network_test_vars(request: pytest.FixtureRequest) -> Dict[str, Any]:
    """Provide the network test vars.

    :param request: The request
    :returns: The network test vars
    """
    requesting_test = Path(request.node.nodeid)

    test_fixture_directory = Path(
        Path(requesting_test.parts[0]) / "integration/fixtures" / Path(*requesting_test.parts[1:])
    ).resolve()
    test_mode = os.environ.get("ANSIBLE_NETWORK_TEST_MODE", "playback").lower()

    play_vars = {
        "ansible_network_test_parameters": {
            "fixture_directory": str(test_fixture_directory),
            "match_threshold": 0.90,
            "mode": test_mode,
        }
    }
    return play_vars


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add options to pytest.

    :param parser: The pytest argument parser
    """
    parser.addoption(
        "--cml-lab",
        action="store",
        required=True,
        help="The CML lab to use",
    )
    parser.addoption(
        "--integration-tests-path",
        action="store",
        required=True,
        help="The integration test path",
    )
    parser.addoption(
        "--role-includes",
        action="store",
        help="The comma delimited positive search substrings to filter the roles",
    )
    parser.addoption(
        "--role-excludes",
        action="store",
        help="The comma delimited negative search substring to filter the roles",
    )


OPTIONS = None


def pytest_configure(config: pytest.Config) -> None:
    """Make cmdline arguments available.

    :param config: The pytest configuration object
    """
    global OPTIONS  # pylint: disable=global-statement
    OPTIONS = config.option
    log_map = {
        0: logging.CRITICAL,
        1: logging.ERROR,
        2: logging.WARNING,
        3: logging.INFO,
        4: logging.DEBUG,
    }
    level = log_map.get(config.option.verbose)
    logging.basicConfig(level=level)
    logger.debug("Logging initialized")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Generate tests.

    :param metafunc: The pytest metafunc object
    :raises Exception: If the options have not been set
    """
    if "integration_test_path" in metafunc.fixturenames:
        if not OPTIONS:
            raise Exception("pytest_configure not called")
        rootdir = Path(OPTIONS.integration_tests_path)
        roles = [path for path in Path(rootdir).iterdir() if path.is_dir()]
        # test_ids = [role.name for role in roles]
        tests = []
        for role in roles:
            reason = ""
            if OPTIONS.role_includes:
                includes = [name.strip() for name in OPTIONS.role_includes.split(",")]
                for include in includes:
                    if include not in role.name:
                        reason = "Role not included by filter"
            if OPTIONS.role_excludes and not reason:
                excludes = [name.strip() for name in OPTIONS.role_excludes.split(",")]
                for exclude in excludes:
                    if exclude in role.name:
                        reason = "Role excluded by filter"
            if reason:
                param = pytest.param(role, id=role.name, marks=pytest.mark.skip(reason=reason))
            else:
                param = pytest.param(role, id=role.name)
            tests.append(param)

        metafunc.parametrize("integration_test_path", tests)


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


def playbook(role: str) -> List[Dict[str, object]]:
    """Return the playbook.

    :param role: The role's path
    :returns: The playbook
    """
    task = {"name": f"Run role {role}", "include_role": {"name": role}}
    play = {"hosts": "all", "gather_facts": False, "tasks": [task]}
    playbook_obj = [play]
    return playbook_obj


@pytest.fixture(scope="session", name="env_vars")
def required_environment_variables() -> Dict[str, str]:
    """Return the required environment variables.

    :raises Exception: If the environment variables are not set
    :returns: The required environment variables
    """
    variables = {
        "cml_host": os.environ.get("VIRL_HOST"),
        "cml_ui_user": os.environ.get("VIRL_USERNAME"),
        "cml_ui_password": os.environ.get("VIRL_PASSWORD"),
        "cml_ssh_user": os.environ.get("CML_SSH_USER"),
        "cml_ssh_password": os.environ.get("CML_SSH_PASSWORD"),
        "cml_ssh_port": os.environ.get("CML_SSH_PORT"),
        "network_os": os.environ.get("ANSIBLE_NETWORK_OS"),
    }
    if not all(variables.values()):
        raise Exception("CML environment variables not set")

    return variables  # type: ignore[return-value]


def _github_action_log(message: str) -> None:
    """Log a message to GitHub Actions.

    :param message: The message
    """
    if os.environ.get("GITHUB_ACTIONS"):
        _print(message)


def _print(message: str) -> None:
    """Print a message and flush.

    This ensures the message doesn't get buffered and mixed in the test stdout.

    :param message: The message
    """
    print(f"\n{message}", flush=True)


@pytest.fixture(scope="session", name="appliance_dhcp_address")
def _appliance_dhcp_address(env_vars: Dict[str, str]) -> Generator[str, None, None]:
    """Build the lab and collect the appliance DHCP address.

    :param env_vars: The environment variables
    :raises Exception: Missing environment variables, lab, or appliance
    :yields: The appliance DHCP address
    """
    _github_action_log("::group::Starting lab provisioning")

    _print("Starting lab provisioning")

    try:

        if not OPTIONS:
            raise Exception("Missing CML lab")
        lab_file = OPTIONS.cml_lab
        if not os.path.exists(lab_file):
            raise Exception(f"Missing lab file '{lab_file}'")

        start = time.time()
        cml = CmlWrapper(
            host=env_vars["cml_host"],
            username=env_vars["cml_ui_user"],
            password=env_vars["cml_ui_password"],
        )
        cml.bring_up(file=lab_file)
        lab_id = cml.current_lab_id

        virsh = VirshWrapper(
            host=env_vars["cml_host"],
            user=env_vars["cml_ssh_user"],
            password=env_vars["cml_ssh_password"],
            port=int(env_vars["cml_ssh_port"]),
        )

        try:
            ip_address = virsh.get_dhcp_lease(lab_id)
        except Exception as exc:
            virsh.close()
            cml.remove()
            raise Exception(f"Failed to get DHCP lease for the appliance: {str(exc)}") from exc

        end = time.time()
        _print(f"Elapsed time to provision {end - start} seconds")

    except Exception as exc:
        logger.error("Failed to provision lab")
        _github_action_log("::endgroup::")
        raise Exception(f"Failed to provision lab: {str(exc)}") from exc

    virsh.close()
    _github_action_log("::endgroup::")

    yield ip_address

    _github_action_log("::group::Removing lab")
    cml.remove()
    _github_action_log("::endgroup::")


@pytest.fixture
def ansible_project(
    appliance_dhcp_address: str,
    env_vars: Dict[str, str],
    integration_test_path: Path,
    tmp_path: Path,
) -> AnsibleProject:
    """Build the ansible project.

    :param appliance_dhcp_address: The appliance DHCP address
    :param env_vars: The environment variables
    :param integration_test_path: The integration test path
    :param tmp_path: The temporary path
    :returns: The ansible project
    """
    octets = appliance_dhcp_address.split(".")
    ssh_port = 2000 + int(octets[-1])
    _https_port = 4000 + int(octets[-1])
    http_port = 8000 + int(octets[-1])
    _netconf_port = 3000 + int(octets[-1])

    inventory = _inventory(
        network_os=env_vars["network_os"],
        host=env_vars["cml_host"],
        username="ansible",
        password="ansible",
        port=ssh_port,
        httpapi_port=http_port,
    )
    inventory_path = tmp_path / "inventory.json"
    with inventory_path.open(mode="w", encoding="utf-8") as fh:
        json.dump(inventory, fh)
    playbook_contents = playbook(str(integration_test_path))
    playbook_path = tmp_path / "site.json"
    with playbook_path.open(mode="w", encoding="utf-8") as fh:
        json.dump(playbook_contents, fh)
    _print(f"Inventory path: {inventory_path}")
    _print(f"Playbook path: {playbook_path}")

    return AnsibleProject(
        playbook=playbook_path,
        inventory=inventory_path,
        directory=tmp_path,
        role=integration_test_path.name,
        log_file=Path.home() / "test_logs" / f"{integration_test_path.name}.log",
        playbook_artifact=Path.home()
        / "test_logs"
        / "{playbook_status}"
        / f"{integration_test_path.name}.json",
    )


@pytest.fixture
def environment() -> Dict[str, Any]:
    """Build the environment, adding the virtual environment if present.

    :returns: The environment
    """
    env = os.environ.copy()
    if "VIRTUAL_ENV" in os.environ:
        env["PATH"] = os.path.join(os.environ["VIRTUAL_ENV"], "bin") + os.pathsep + env["PATH"]
    return env


@pytest.hookimpl(tryfirst=True, hookwrapper=True)  # type: ignore[misc]
def pytest_runtest_makereport(
    item: pytest.Item, *_args: Any, **_kwargs: Any
) -> Generator[None, pluggy_result, None]:
    """Add additional information to the test item.

    :param item: The test item
    :param _args: The positional arguments
    :param _kwargs: The keyword arguments
    :yields: To all other hooks
    """
    # execute all other hooks to obtain the report object
    outcome = yield
    rep = outcome.get_result()

    # set a report attribute for each phase of a call, which can
    # be "setup", "call", "teardown"

    setattr(item, "rep_" + rep.when, rep)


@pytest.fixture(autouse=True)
def github_log(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Log a message to GitHub Actions.

    :param request: The request
    :yields: To the test
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        yield
    else:
        name = request.node.name

        _github_action_log(f"::group::Run integration test: '{name}'")
        yield

        if hasattr(request.node, "rep_call"):
            if request.node.rep_setup.passed and request.node.rep_call.failed:
                _github_action_log("::endgroup::")
                msg = f"Integration test failure: '{name}'"
                _github_action_log(f"::error title={msg}::{msg}")
                return
        _github_action_log("::endgroup::")

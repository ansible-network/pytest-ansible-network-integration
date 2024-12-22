# cspell:ignore nodeid, levelname, cmlutils
"""Common fixtures for tests."""

import json
import logging
import os
import time

from pathlib import Path
from typing import Any
from typing import Dict
from typing import Generator

import pytest

from pluggy._result import _Result as pluggy_result

from .defs import AnsibleProject
from .defs import CmlWrapper
from .defs import VirshWrapper
from .exceptions import PytestNetworkError
from .utils import _github_action_log
from .utils import _inventory
from .utils import _print
from .utils import calculate_ports
from .utils import playbook


# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", # cspell:ignore levelname
    handlers=[logging.FileHandler("pytest-network.log"), logging.StreamHandler()],
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def network_test_vars(request: pytest.FixtureRequest) -> Dict[str, Any]:
    """Provide the network test vars.

    This fixture generates and returns a dictionary of network test variables
    for each test function. It constructs paths and retrieves environment
    variables needed for the tests.

    :param request: The pytest fixture request object, which provides information
                    about the requesting test function
    :raises PytestNetworkError: If there is an error creating the network test vars.
    :returns: A dictionary containing network test variables.
    """
    try:
        requesting_test = Path(request.node.nodeid)
        logger.debug(f"Test path: {requesting_test}")

        test_fixture_directory = Path(
            Path(requesting_test.parts[0])
            / "integration/fixtures"
            / Path(*requesting_test.parts[1:])
        ).resolve()
        logger.debug(f"Test fixture directory: {test_fixture_directory}")

        test_mode = os.environ.get("ANSIBLE_NETWORK_TEST_MODE", "playback").lower()
        logger.debug(f"Test mode: {test_mode}")

        play_vars = {
            "ansible_network_test_parameters": {
                "fixture_directory": str(test_fixture_directory),
                "match_threshold": 0.90,
                "mode": test_mode,
            }
        }
        logger.info("Network test vars successfully created")
        return play_vars

    except Exception as e:
        logger.error(f"Error creating network test vars: {e}")
        raise PytestNetworkError(f"Error creating network test vars: {e}")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add options to pytest.

    :param parser: The pytest argument parser
    """
    parser.addoption(
        "--cml-lab",
        action="store",
        help="The CML lab to use",
    )
    parser.addoption(
        "--integration-tests-path",
        action="store",
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
    """Make command-line arguments available globally.

    This function is a pytest hook that is called after command-line options
    have been parsed. It makes the command-line options available globally
    by storing them in the `OPTIONS` variable.

    :param config: The pytest configuration object, which contains the parsed
                    command-line options and other configuration values.
    """
    global OPTIONS  # pylint: disable=global-statement
    OPTIONS = config.option


@pytest.fixture(scope="session", name="env_vars")
def required_environment_variables() -> Dict[str, str]:
    """Return the required environment variables for the CML environment.

    This fixture retrieves the necessary environment variables for the CML
    environment and returns them as a dictionary. If any
    of the required environment variables are not set, it raises a
    PytestNetworkError.

    :raises PytestNetworkError: If any of the required environment variables are not set.
    :returns: A dictionary containing the required environment variables.
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
        logger.error("CML environment variables not set")
        raise PytestNetworkError("CML environment variables not set")

    # Get the device username and password, default to "ansible" if not found.
    variables["device_username"] = os.environ.get("DEVICE_USERNAME", "ansible")
    variables["device_password"] = os.environ.get("DEVICE_PASSWORD", "ansible")

    return variables  # type: ignore[return-value]


@pytest.fixture
def environment() -> Dict[str, Any]:
    """Build and return the environment variables for the tests.

    This fixture creates a copy of the current environment variables and adds
    the virtual environment's bin directory to the PATH if a virtual environment
    is active. It also disables warnings about localhost for Ansible.

    :returns: A dictionary containing the environment variables for the tests.
    """
    env = os.environ.copy()
    if "VIRTUAL_ENV" in os.environ:
        env["PATH"] = os.path.join(os.environ["VIRTUAL_ENV"], "bin") + os.pathsep + env["PATH"]
    # Disable warnings about localhost, since these are tests
    env["ANSIBLE_LOCALHOST_WARNING"] = "False"
    return env


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, *_args: Any, **_kwargs: Any
) -> Generator[None, pluggy_result, None]:  # type: ignore[type-arg]
    """Add additional information to the test item.

    This hook implementation is used to add additional information to the test
    item during the test run. It sets a report attribute for each phase of a call,
    which can be "setup", "call", or "teardown".

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

        _github_action_log(f"::group::Integration test stdout: '{name}'")
        yield

        if hasattr(request.node, "rep_call"):
            if request.node.rep_setup.passed and request.node.rep_call.failed:
                _github_action_log("::endgroup::")
                gh_job = os.environ.get("GITHUB_JOB", "Unknown job")
                title = f"{gh_job}: {name}"
                msg = f"Integration test failure: '{name}'"
                _github_action_log(f"::error title={title}::{msg}")

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
    logger.info("Building the Ansible project")
    ports = calculate_ports(appliance_dhcp_address)

    inventory = _inventory(
        network_os=env_vars["network_os"],
        host=env_vars["cml_host"],
        username=env_vars["device_username"],
        password=env_vars["device_password"],
        port=ports["ssh_port"],  # ssh_port
        httpapi_port=ports["http_port"],  # http_port
    )
    logger.debug(f"Generated inventory: {inventory}")

    inventory_path = tmp_path / "inventory.json"
    with inventory_path.open(mode="w", encoding="utf-8") as fh:
        json.dump(inventory, fh)
    logger.debug(f"Inventory written to {inventory_path}")

    playbook_contents = playbook(hosts="all", role=str(integration_test_path))
    playbook_path = tmp_path / "site.json"
    with playbook_path.open(mode="w", encoding="utf-8") as fh:
        json.dump(playbook_contents, fh)
    logger.debug(f"Playbook written to {playbook_path}")

    _print(f"Inventory path: {inventory_path}")
    _print(f"Playbook path: {playbook_path}")

    ansible_project = AnsibleProject(
        collection_doc_cache=tmp_path / "collection_doc_cache.db",
        directory=tmp_path,
        inventory=inventory_path,
        log_file=Path.home() / "test_logs" / f"{integration_test_path.name}.log",
        playbook=playbook_path,
        playbook_artifact=Path.home()
        / "test_logs"
        / "{playbook_status}"
        / f"{integration_test_path.name}.json",
        role=integration_test_path.name,
    )
    logger.info("Ansible project created successfully")
    return ansible_project


@pytest.fixture
def localhost_project(
    integration_test_path: Path,
    tmp_path: Path,
) -> AnsibleProject:
    """Build an ansible project with only implicit localhost.

    :param integration_test_path: The integration test path
    :param tmp_path: The temporary path
    :returns: The ansible project
    """
    logger.debug("Building the Ansible project for localhost")

    playbook_contents = playbook(hosts="localhost", role=str(integration_test_path))
    playbook_path = tmp_path / "site.json"
    with playbook_path.open(mode="w", encoding="utf-8") as fh:
        json.dump(playbook_contents, fh)
    logger.debug(f"Playbook written to {playbook_path}")

    _print(f"Playbook path: {playbook_path}")

    ansible_project = AnsibleProject(
        collection_doc_cache=tmp_path / "collection_doc_cache.db",
        directory=tmp_path,
        log_file=Path.home() / "test_logs" / f"{integration_test_path.name}.log",
        playbook=playbook_path,
        playbook_artifact=Path.home()
        / "test_logs"
        / "{playbook_status}"
        / f"{integration_test_path.name}.json",
        role=integration_test_path.name,
    )
    logger.info("Ansible project for localhost created successfully")
    return ansible_project


@pytest.fixture(scope="session", name="appliance_dhcp_address")
def _appliance_dhcp_address(env_vars: Dict[str, str]) -> Generator[str, None, None]:
    """Build the lab and collect the appliance DHCP address.

    This fixture provisions the lab using CML and retrieves the DHCP address
    of the appliance. It ensures the lab is properly set up and tears it down
    after the tests are completed.

    :param env_vars: The environment variables required for CML and SSH.
    :raises PytestNetworkError: If there are missing environment variables, lab file, or appliance.
    :yields: The appliance DHCP address.
    """
    _github_action_log("::group::Starting lab provisioning")
    _print("Starting lab provisioning")

    try:
        if not OPTIONS:
            raise PytestNetworkError("Missing CML lab options")

        lab_file = OPTIONS.cml_lab
        if not os.path.exists(lab_file):
            raise PytestNetworkError(f"Missing lab file '{lab_file}'")

        start = time.time()
        cml = CmlWrapper(
            host=env_vars["cml_host"],
            username=env_vars["cml_ui_user"],
            password=env_vars["cml_ui_password"],
        )
        cml.bring_up(file=lab_file)
        lab_id = cml.current_lab_id
        logger.debug("Lab ID: %s", lab_id)

        virsh = VirshWrapper(
            host=env_vars["cml_host"],
            user=env_vars["cml_ssh_user"],
            password=env_vars["cml_ssh_password"],
            port=int(env_vars["cml_ssh_port"]),
        )

        try:
            ip_address = virsh.get_dhcp_lease(lab_id)
        except PytestNetworkError as exc:
            logger.error("Failed to get DHCP lease for the appliance")
            virsh.close()
            cml.remove()
            raise PytestNetworkError("Failed to get DHCP lease for the appliance") from exc

        end = time.time()
        _print(f"Elapsed time to provision: {end - start} seconds")
        logger.info(f"Elapsed time to provision: {end - start} seconds")

    except PytestNetworkError as exc:
        logger.error("Failed to provision lab: %s", exc)
        _github_action_log("::endgroup::")
        raise

    finally:
        virsh.close()
        _github_action_log("::endgroup::")

    yield ip_address

    _github_action_log("::group::Removing lab")
    try:
        cml.remove()
    except PytestNetworkError as exc:
        logger.error("Failed to remove lab: %s", exc)
        raise
    finally:
        _github_action_log("::endgroup::")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Generate tests based on the integration test paths.

    This function is a pytest hook that generates tests dynamically based on
    the integration test paths. It filters the roles based on the include and
    exclude options provided via command-line arguments.

    :param metafunc: The pytest metafunc object.
    :raises PytestNetworkError: If the options have not been set.
    """
    if "integration_test_path" not in metafunc.fixturenames:
        return

    if not OPTIONS:
        raise PytestNetworkError("pytest_configure not called")

    rootdir = Path(OPTIONS.integration_tests_path)
    roles = [path for path in rootdir.iterdir() if path.is_dir()]
    logger.info("Found roles: %s", [role.name for role in roles])

    tests = []
    for role in roles:
        reason = _filter_role(role)
        if reason:
            param = pytest.param(role, id=role.name, marks=pytest.mark.skip(reason=reason))
        else:
            param = pytest.param(role, id=role.name)
        tests.append(param)

    metafunc.parametrize("integration_test_path", tests)
    logger.info("Generated tests: %s", [test.id for test in tests])


def _filter_role(role: Path) -> str:
    """Filter roles based on include and exclude options.

    :param role: The role path.
    :return: The reason for skipping the role, or an empty string if not skipped.
    """
    if OPTIONS.role_includes:
        includes = [name.strip() for name in OPTIONS.role_includes.split(",")]
        if not any(include in role.name for include in includes):
            logger.debug("Role %s not included by filter", role.name)
            return "Role not included by filter"

    if OPTIONS.role_excludes:
        excludes = [name.strip() for name in OPTIONS.role_excludes.split(",")]
        if any(exclude in role.name for exclude in excludes):
            logger.debug("Role %s excluded by filter", role.name)
            return "Role excluded by filter"

    return ""

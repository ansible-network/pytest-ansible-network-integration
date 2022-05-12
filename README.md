# pytest-ansible-network-integration

An experimental pytest plugin designed to run ansible network integration tests against an appliance defined in an CML lab topology.

## Lab provisioning

The pytest plugin will bring up the lab automatically. If you wish to reuse a lab for multiple tests, it can be brought up prior to using pytest with:

```console
$ cml up -f tests/integration/labs/single.yaml
```

The lab will remain after the test run, please remember to delete it when you have finished. This can be done at the command line with:

```console
$ cml rm --force --no-confirm
```

## Required environment variables:

- ANSIBLE_NETWORK_OS
- CML_SSH_PASSWORD
- CML_SSH_PORT
- CML_SSH_USER
- CML_VERIFY_CERT
- VIRL_HOST
- VIRL_PASSWORD
- VIRL_USERNAME

### Sample environment variables

```console
$ cat .env

ANSIBLE_NETWORK_OS=cisco.nxos.nxos
CML_SSH_PASSWORD=secret'
CML_SSH_PORT=1122
CML_SSH_USER=sysadmin
CML_VERIFY_CERT=False
VIRL_HOST=1.2.3.4
VIRL_PASSWORD='secret'
VIRL_USERNAME=admin
```

By placing the environment variables in a `.env` file, they will be used by pytest running within vscode. To add these environment variables from the `.env` file to you environment:

```console
$ export $(cat .env | xargs)
```

## Required pytest command line parameters:

- --integration-tests-path
- ---cml-lab

## Sample pyproject.toml:

```toml
[tool.pytest.ini_options]
addopts = "-s -vvv --integration-tests-path=tests/integration/targets --cml-lab=./tests/integration/labs/single.yaml"
testpaths = ["tests"]
filterwarnings = [
  'ignore:AnsibleCollectionFinder has already been configured',
  'ignore:_AnsibleCollectionFinder.find_spec().*',
]

```

"""Forge smart contracte development toolchain integration.

- Compile and deploy smart contracts using Forge

- Verify smart contracts on Etherscan

- See `Foundry book <https://book.getfoundry.sh/>`__ for more information.
"""

import datetime
import logging
import os

from pathlib import Path
from shutil import which
from subprocess import DEVNULL, PIPE
from typing import Tuple

import psutil
from eth_account.signers.local import LocalAccount
from eth_typing import ChecksumAddress, HexAddress, HexStr
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import register_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import is_anvil
from eth_defi.trace import assert_transaction_success_with_explanation


logger = logging.getLogger(__name__)


#: Crash unless forge completes in 4 minutes
#:
DEFAULT_TIMEOUT = 4 * 60


class ForgeFailed(Exception):
    """Forge command failed."""


#: Because of Forge's
#:
_last_deploy: datetime.datetime | None = None


def _exec_cmd(
    cmd_line: list[str],
    censored_command: str,
    timeout=DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> Tuple[str, str]:
    """Execute the command line.

    :param timeout:
        Timeout in seconds

    :return:
        Tuple(deployed contract address, tx hash)
    """

    for x in cmd_line:
        assert type(x) == str, f"Got non-string in command line: {x} in {cmd_line}"

    # out = DEVNULL if sys.platform == "win32" else PIPE
    out = PIPE  # TODO: Are we set on a failure on Windows
    proc = psutil.Popen(cmd_line, stdin=DEVNULL, stdout=out, stderr=out)
    result = proc.wait(timeout)

    output = proc.stdout.read().decode("utf-8") + proc.stderr.read().decode("utf-8")

    if result != 0:
        if "No files changed, compilation skipped" not in output:
            raise ForgeFailed(f"forge return code {result} when running: {censored_command}\nOutput is:\n{output}")

    logger.debug("forge result:\n%s", output)

    address = tx_hash = None

    for line in output.split("\n"):
        # Deployed to: 0x604Da6680Cb97A87403600B9AafBE60eeda97CA4
        if line.startswith("Deployed to: "):
            address = line.split(":")[1].strip()

        if line.startswith("Transaction hash: "):
            tx_hash = line.split(":")[1].strip()

    if not (address and tx_hash):
        raise ForgeFailed(f"Could not parse forge output:\n{output}\nCommand line was:{' '.join(cmd_line)}")

    return address, tx_hash


def deploy_contract_with_forge(
    web3: Web3,
    project_folder: Path,
    contract_file: Path | str,
    contract_name: str,
    deployer: HotWallet | LocalAccount,
    constructor_args: list[str] | None = None,
    etherscan_api_key: str | None = None,
    register_for_tracing=True,
    timeout=DEFAULT_TIMEOUT,
    wait_for_block_confirmations=0,
    verify_delay=20,
    verify_retries=9,
    verbose=False,
    contract_file_out: Path | str | None = None,
) -> Tuple[Contract, HexBytes]:
    """Deploy and verify smart contract with Forge.

    - The smart contracts must be developed with Foundry tool chain and its `forge` command

    - Uses Forge to verify the contract on Etherscan

    - For normal use :py:func:`deploy_contract` is much easier

    Example:

    .. code-block:: python

        guard, tx_hash = deploy_contract_with_forge(
            web3,
            CONTRACTS_ROOT / "guard",  # Foundry projec path
            "GuardV0.sol",  # src/GuardV0.sol
            f"GuardV0",  # GuardV0 is the smart contract name
            deployer,  # Local account with a private key we use for the deployment
            etherscan_api_key=etherscan_api_key,  # Etherscan API key we use for the verification
        )
        logger.info("GuardV0 is %s deployed at %s", guard.address, tx_hash.hex())

        # Test the deployed contract
        assert guard.functions.getInternalVersion().call() == 1

    Assumes standard Foundry project layout with foundry.toml, src and out.

    See

    - `Foundry book <https://book.getfoundry.sh/>`__ for more information

    - :py:func:`eth_defi.deploy.deploy_contract` for simple, non-verified contract deployments

    :param web3:
        Web3 instance

    :param deployer:
        Deployer tracked as a hot wallet.

        We need to be able to manually track the nonce across multiple contract deployments.

    :param project_folder:
        Foundry project with `foundry.toml` in the root.

    :param contract_file:
        Contract path relative to the project folder.

        E.g. `TermsOfService.sol`.

    :param contract_name:
        The smart contract name within the file.

        E.g. `TermsOfService`.

    :param constructor_args:
        Other arguments to pass to the contract's constructor.

        Need to be able to stringify these for forge.

    :param etherscan_api_key:
        Needed for the source code verification on Etherscan and related services.

        You need a private API key.

        E.g. `3F3H8....`.

    :param register_for_tracing:
        Make the symbolic contract information available on web3 instance.

        See :py:func:`get_contract_registry`

    :param wait_for_block_confirmations:
        Currently not used.

    :param verbose:
        Try to be extra verbose with Forge output to pin point errors

    :raise ForgeFailed:
        In the case we could not deploy the contract.

        - Running forge failed
        - Transaction could not be confirmed

    :return:
        Contract and deployment tx hash.

    """
    assert isinstance(project_folder, Path), f"Got non-Path project folder: {type(project_folder)} {project_folder}"
    assert type(contract_name) == str

    if isinstance(deployer, HotWallet):
        deployer.sync_nonce(web3)

    if constructor_args is None:
        constructor_args = []

    if type(contract_file) == str:
        contract_file = Path(contract_file)

    assert isinstance(contract_file, Path)
    assert type(constructor_args) in (list, tuple)

    json_rpc_url = web3.provider.endpoint_uri

    forge = which("forge")
    assert forge is not None, "No forge command in path, needed for the contract deployment"

    src_contract_file = Path("src") / contract_file

    if isinstance(deployer, HotWallet):
        private_key = deployer.private_key.hex()
        nonce = str(deployer.allocate_nonce())
    elif isinstance(deployer, LocalAccount):
        private_key = deployer._private_key.hex()
        nonce = str(web3.eth.get_transaction_count(deployer.address))
    else:
        raise NotImplementedError(f"Unsupported deployer: {deployer}")

    cmd_line = [
        forge,
        "create",
        "--broadcast",
        "--rpc-url",
        json_rpc_url,
        "--nonce",
        nonce,
    ]

    if verbose:
        cmd_line.append("-vvv")

    if etherscan_api_key:
        if is_anvil(web3):
            logger.warning("Etherscan verification skipped, running on a local fork")
        else:
            logger.info("Doing Etherscan verification with %d retries", verify_retries)
            # Tuned retry parameters
            # https://github.com/foundry-rs/foundry/issues/6953
            cmd_line += [
                "--etherscan-api-key",
                etherscan_api_key,
                "--verify",
                "--retries",
                str(verify_retries),
                "--delay",
                str(verify_delay),
            ]

    cmd_line += [f"{src_contract_file}:{contract_name}"]

    if constructor_args:
        cmd_line += ["--constructor-args"]
        for arg in constructor_args:
            cmd_line.append(arg)

    try:
        censored_command = " ".join(cmd_line)
    except TypeError as e:
        # Be helpful with None error
        raise TypeError(f"Could not splice command line: {cmd_line}") from e

    logger.info(
        "Deploying a contract with forge. Working directory %s, forge command: %s",
        project_folder.resolve(),
        censored_command,
    )

    # Inject private key after logging
    cmd_line = [
        forge,
        "create",
        "--private-key",
        private_key,
    ] + cmd_line[2:]

    # Py 3.11 only
    # with contextlib.chdir(project_folder):
    old_path = os.getcwd()
    try:
        os.chdir(project_folder)

        assert (project_folder / "foundry.toml").exists(), f"foundry.toml missing: {project_folder}"

        assert src_contract_file.suffix == ".sol", f"Not Solidity source file: {contract_file}"
        assert src_contract_file.exists(), f"Contract does not exist: {src_contract_file}, current working directory is {os.getcwd()}"

        # Run forge
        contract_address, tx_hash = _exec_cmd(cmd_line, timeout=timeout, censored_command=censored_command, verbose=verbose)

        if contract_file_out is None:
            contract_file_out = contract_file

        # Check we produced an ABI file, or was created earlier
        contract_abi = project_folder / "out" / contract_file_out / f"{contract_name}.json"
        assert contract_abi.exists(), f"Forge did not produce ABI file: {contract_abi.resolve()}"
    finally:
        os.chdir(old_path)

    # Mad Web3.py API
    contract_address = ChecksumAddress(HexAddress(HexStr(contract_address)))
    instance = get_deployed_contract(web3, contract_abi, contract_address)

    if register_for_tracing:
        instance.name = contract_name
        register_contract(web3, contract_address, instance)

    tx_hash = HexBytes(tx_hash)
    assert_transaction_success_with_explanation(
        web3,
        tx_hash,
        RaisedException=ForgeFailed,
    )

    return instance, tx_hash

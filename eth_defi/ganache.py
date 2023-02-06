"""Ganache EVM test backend and mainnet forking.

This module contains utilities to automatically launch and
manipulate `ganache-cli` process.

You need to have `ganache-cli` installed in order to use these.

How to install ganache-cli using npm:

.. code-block:: shell

    npm install -g ganache

For more information about Ganache see

- `Ganache CLI command line documentation <https://github.com/trufflesuite/ganache#documentation>`_

- `Aave Web.py example <https://github.com/PatrickAlphaC/aave_web3_py>`_

- `QuickNode how to fork mainnet with Ganache tutorial <https://www.quicknode.com/guides/web3-sdks/how-to-fork-ethereum-blockchain-with-ganache>`_

`Most of this code is lifted from Brownie project (MIT) <https://github.com/eth-brownie/brownie/blob/master/brownie/network/rpc/ganache.py>`_
and it is not properly cleaned up yet.

"""

import datetime
import logging
import re
import shutil
import sys
import time
import warnings
from dataclasses import dataclass
from subprocess import DEVNULL, PIPE
from typing import Dict, List, Tuple, Union

import psutil
import requests

from eth_typing import HexAddress
from hexbytes import HexBytes
from psutil import NoSuchProcess
from web3 import HTTPProvider, Web3
from web3.types import Wei

from eth_defi.utils import is_localhost_port_listening


logger = logging.getLogger(__name__)


EVM_EQUIVALENTS = {"atlantis": "byzantium", "agharta": "petersburg"}

# https://github.com/trufflesuite/ganache
CLI_FLAGS = {
    "7": {
        "port": "--server.port",
        "gas_limit": "--miner.blockGasLimit",
        "accounts": "--wallet.totalAccounts",
        "evm_version": "--hardfork",
        "fork": "--fork.url",
        "mnemonic": "--wallet.mnemonic",
        "account_keys_path": "--wallet.accountKeysPath",
        "block_time": "--miner.blockTime",
        "default_balance": "--wallet.defaultBalance",
        "time": "--chain.time",
        "unlock": "--wallet.unlockedAccounts",
        "network_id": "--chain.networkId",
        "chain_id": "--chain.chainId",
        "unlimited_contract_size": "--chain.allowUnlimitedContractSize",
        "quiet": "--logging.quiet",
    },
    "<=6": {
        "port": "--port",
        "gas_limit": "--gasLimit",
        "accounts": "--accounts",
        "evm_version": "--hardfork",
        "fork": "--fork",
        "mnemonic": "--mnemonic",
        "account_keys_path": "--acctKeys",
        "block_time": "--blockTime",
        "default_balance": "--defaultBalanceEther",
        "time": "--time",
        "unlock": "--unlock",
        "network_id": "--networkId",
        "chain_id": "--chainId",
        "unlimited_contract_size": "--allowUnlimitedContractSize",
    },
}

EVM_VERSIONS = ["byzantium", "constantinople", "petersburg", "istanbul"]

#: The default hardfork rules used by Ganache
EVM_DEFAULT = "london"


class NoGanacheInstalled(Exception):
    """We could not launch because ganache-cli command is missing"""


class InvalidArgumentWarning(Warning):
    """Warned when there are issued with ganache-cli command line."""


def _launch(cmd: str, **kwargs: Dict) -> Tuple[psutil.Popen, List[str]]:
    """Launches the RPC client.

    Args:
        cmd: command string to execute as subprocess"""
    if sys.platform == "win32" and not cmd.split(" ")[0].endswith(".cmd"):
        if " " in cmd:
            cmd = cmd.replace(" ", ".cmd ", 1)
        else:
            cmd += ".cmd"
    cmd_list = cmd.split(" ")

    ganache_executable = cmd_list[0]

    found = shutil.which(ganache_executable)
    if not found:
        raise NoGanacheInstalled(f"Could not find ganache-cli installation: {ganache_executable} - are you sure it is installed?")

    ganache_version = _get_ganache_version(ganache_executable)

    if ganache_version <= 6:
        cli_flags = CLI_FLAGS["<=6"]
    else:
        cli_flags = CLI_FLAGS["7"]
        # this flag must be true so that reverting tx's return a
        # more verbose output similar to what ganache 6 produced
        cmd_list.extend(["--chain.vmErrorsOnRPCResponse", "true"])

    kwargs.setdefault("evm_version", EVM_DEFAULT)  # type: ignore
    if kwargs["evm_version"] in EVM_EQUIVALENTS:
        kwargs["evm_version"] = EVM_EQUIVALENTS[kwargs["evm_version"]]  # type: ignore
    kwargs = _validate_cmd_settings(kwargs)
    for key, value in [(k, v) for k, v in kwargs.items() if v]:
        if key == "unlock":
            if not isinstance(value, list):
                value = [value]  # type: ignore
            for address in value:
                if isinstance(address, int):
                    address = HexBytes(address.to_bytes(20, "big")).hex()
                cmd_list.extend([cli_flags[key], address])
        else:
            try:
                # Handle boolean options
                if value is True:
                    cmd_list.append(cli_flags[key])
                elif value is not False:
                    cmd_list.extend([cli_flags[key], str(value)])
            except KeyError:
                warnings.warn(
                    f"Ignoring invalid commandline setting for ganache-cli: '{key}' with value '{value}'.",
                    InvalidArgumentWarning,
                )
    out = DEVNULL if sys.platform == "win32" else PIPE

    logger.info("Launching ganache-cli: %s", " ".join(cmd_list))
    return psutil.Popen(cmd_list, stdin=DEVNULL, stdout=out, stderr=out), cmd_list


def _get_ganache_version(ganache_executable: str) -> int:
    ganache_version_proc = psutil.Popen([ganache_executable, "--version"], stdout=PIPE)
    ganache_version_stdout, _ = ganache_version_proc.communicate()
    ganache_version_match = re.search(r"v([0-9]+)\.", ganache_version_stdout.decode())
    if not ganache_version_match:
        raise ValueError("could not read ganache version: {}".format(ganache_version_stdout))
    return int(ganache_version_match.group(1))


def _validate_cmd_settings(cmd_settings: dict) -> dict:
    ganache_keys = set(k for f in CLI_FLAGS.values() for k in f.keys())

    CMD_TYPES = {
        "port": int,
        "gas_limit": int,
        "block_time": int,
        "time": datetime.datetime,
        "accounts": int,
        "evm_version": str,
        "mnemonic": str,
        "account_keys_path": str,
        "fork": str,
        "network_id": int,
        "chain_id": int,
        "quiet": bool,
    }
    for cmd, value in cmd_settings.items():
        if cmd in ganache_keys and cmd in CMD_TYPES.keys() and not isinstance(value, CMD_TYPES[cmd]):
            raise TypeError(f"Wrong type for cmd_settings '{cmd}': {value}. Found {type(value).__name__}, but expected {CMD_TYPES[cmd].__name__}.")

    if "default_balance" in cmd_settings:
        try:
            cmd_settings["default_balance"] = int(cmd_settings["default_balance"])
        except ValueError:
            # convert any input to ether, then format it properly
            default_eth = Wei(cmd_settings["default_balance"]).to("ether")
            cmd_settings["default_balance"] = default_eth.quantize(1) if default_eth > 1 else default_eth.normalize()
    return cmd_settings


@dataclass
class GanacheLaunch:
    """Control ganache-cli processes launched on background.

    Comes with a helpful :py:meth:`close` method when it is time to put Ganache rest.
    """

    #: Which port was bound by the ganache
    port: int

    #: Used command-line to spin up ganache-cli
    cmd: List[str]

    #: Where does Ganache listen to JSON-RPC
    json_rpc_url: str

    #: UNIX process that we opened
    process: psutil.Popen

    def close(self, verbose=False, block=True, block_timeout=30):
        """Kill the ganache-cli process.

        Ganache is pretty hard to kill, so keep killing it until it dies and the port is free again.

        :param block: Block the execution until Ganache has terminated
        :param block_timeout: How long we give for Ganache to clean up after itself
        :param verbose: If set, dump anything in Ganache stdout to the Python logging using level `INFO`.
        """

        process = self.process
        if verbose:
            # TODO: This does not seem to work on macOS,
            # but is fine on Ubuntu on Github CI
            logger.info("Dumping Ganache output")
            if process.poll() is not None:
                output = process.communicate()[0].decode("utf-8")
                for line in output.split("\n"):
                    logger.info(line)

        # process.terminate()
        # Hahahahah, this is Ganache, do you think terminate signal is enough
        try:
            process.kill()
        except NoSuchProcess:
            raise AssertionError("ganache died on its own :(")

        if block:
            deadline = time.time() + 30
            while time.time() < deadline:
                if not is_localhost_port_listening(self.port):
                    # Port released, assume Ganache is gone
                    return

            raise AssertionError(f"Could not terminate ganache in {block_timeout} seconds")


def fork_network(
    json_rpc_url: str,
    unlocked_addresses: List[Union[HexAddress, str]] = [],
    cmd="ganache-cli",
    port=19999,
    evm_version=EVM_DEFAULT,
    block_time=0,
    quiet=False,
    launch_wait_seconds=20.0,
) -> GanacheLaunch:
    """Creates the ganache "fork" of given JSON-RPC endpoint.

    .. warning::

        This function is not recommended due to stability issues with Ganache.
        Use :py:func:`eth_defi.anvil.fork_network_anvil` instead.

    Forking a mainnet is common way to test against live deployments.
    This function invokes `ganache-cli` command and tells it to fork a given JSON-RPC endpoint.

    A subprocess is started on the background. To stop this process, call :py:meth:`eth_defi.ganache.GanacheLaunch.close`.
    This function waits `launch_wait_seconds` in order to `ganache-cli` process to start
    and complete the chain fork.

    .. note ::

        Currently only supports HTTP JSON-RPC connections.

    .. warning ::

        Forking a network with ganache-cli is a slow process. It is recommended
        that you use fast Ethereum Tester based testing if possible.

    Here is an example that forks BNB chain mainnet and transfer 500 BUSD stablecoin to a test
    account we control:

    .. code-block:: python

        @pytest.fixture()
        def large_busd_holder() -> HexAddress:
            # A random account picked from BNB Smart chain that holds a lot of BUSD.
            # Binance Hot Wallet 6
            return HexAddress(HexStr("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"))


        @pytest.fixture()
        def ganache_bnb_chain_fork(large_busd_holder) -> str:
            # Create a testable fork of live BNB chain.
            mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
            launch = fork_network(
                mainnet_rpc,
                unlocked_addresses=[large_busd_holder])
            yield launch.json_rpc_url
            # Wind down Ganache process after the test is complete
            launch.close()


        @pytest.fixture
        def web3(ganache_bnb_chain_fork: str):
            # Set up a local unit testing blockchain
            return Web3(HTTPProvider(ganache_bnb_chain_fork))


        def test_mainnet_fork_transfer_busd(web3: Web3, large_busd_holder: HexAddress, user_1: LocalAccount):

            # BUSD deployment on BNB chain
            # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
            busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
            busd = busd_details.contract

            # Transfer 500 BUSD to the user 1
            tx_hash = busd.functions.transfer(user_1.address, 500*10**18).transact({"from": large_busd_holder})

            # Because Ganache has instamine turned on by default, we do not need to wait for the transaction
            receipt = web3.eth.get_transaction_receipt(tx_hash)
            assert receipt.status == 1, "BUSD transfer reverted"

            assert busd.functions.balanceOf(user_1.address).call() == 500*10**18

    `See the full example in tests source code <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_ganache.py>`_.

    Polygon needs to set a specific EVM version:

    .. code-block:: python

            mainnet_rpc = os.environ["POLYGON_JSON_RPC"]
            launch = fork_network(mainnet_rpc, evm_version="istanbul")

    If `ganache-cli` refuses to terminate properly, you can kill a process by a port with:

    .. code-block:: shell

        # Kill any process listening to localhost:19999
        kill -SIGKILL $(lsof -ti:19999)

    This function uses Python logging subsystem. If you want to see error/info/debug logs with `pytest` you can do:

    .. code-block:: shell

        pytest --log-cli-level=debug

    For public JSON-RPC endpoints check

    - `BNB chain documentation <https://docs.binance.org/smart-chain/developer/rpc.html>`_

    - `ethereumnodes.com <https://ethereumnodes.com/>`_

    :param cmd: Override `ganache-cli` command. If not given we look up from `PATH`.
    :param json_rpc_url: HTTP JSON-RPC URL of the network we want to fork
    :param unlocked_addresses: List of addresses of which ownership we take to allow test code to transact as them
    :param port: Localhost port we bind for Ganache JSON-RPC
    :param launch_wait_seconds: How long we wait ganache-cli to start until giving up
    :param evm_version: "london" for the default hard fork
    :param block_time:
        How long Ganache takes to mine a block. Default is zero and any RPC transaction
        will immediately return with the transaction inclusion.
        Set to `1` so that you can poll the transaction as you would do with
        a live JSON-RPC node.
    :param quiet:
        Disable extensive logging. If there is a lot of Ganache logging it seems to crash
        on Github CI.

    """

    assert not is_localhost_port_listening(port), f"localhost port {port} occupied - you might have a zombie Ganache around"

    url = f"http://localhost:{port}"

    process, final_cmd = _launch(
        cmd,
        port=port,
        fork=json_rpc_url,
        unlock=unlocked_addresses,
        evm_version=evm_version,
        block_time=block_time,
        quiet=quiet,
    )

    # Wait until Ganache is responsive
    timeout = time.time() + launch_wait_seconds
    current_block = None

    # Use short 1.0s HTTP read timeout here - otherwise requests will wa-it > 10s if something is wrong
    web3 = Web3(HTTPProvider(url, request_kwargs={"timeout": 1.0}))
    while time.time() < timeout:
        if process.poll() is not None:
            output = process.communicate()[0].decode("utf-8")
            for line in output.split("\n"):
                logger.error(line)
            raise AssertionError(f"ganache-cli died on launch, used command was {final_cmd}")

        try:
            current_block = web3.eth.block_number
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            # requests.exceptions.ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))
            time.sleep(0.1)
            continue

    if current_block is None:

        if process.poll() is not None:
            output = process.communicate()[0].decode("utf-8")
            for line in output.split("\n"):
                logger.error(line)
            raise AssertionError(f"ganache-cli died on launch, used command was {final_cmd}")

        logger.error("Could not read the latest block from ganache-cli within %f seconds", launch_wait_seconds)
        raise AssertionError(f"Could not connect to ganache-cli {cmd}: at {url}")

    chain_id = web3.eth.chain_id

    # Use f-string for thousand separator formatting
    logger.info(f"ganache-cli forked network %d, the current block is {current_block:,}, Ganache JSON-RPC is %s", chain_id, url)

    return GanacheLaunch(port, final_cmd, url, process)

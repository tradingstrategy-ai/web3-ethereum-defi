"""Anvil integration.

`Anvil <https://github.com/foundry-rs/foundry/blob/master/anvil/README.md>`__
is a blazing-fast local testnet node implementation in Rust. Anvil may be used as an alternative to Anvil.

- Anvil is mostly used in mainnet fork test cases

This code has been lifted from Bronwnie project.

To install Anvil:

.. code-block:: shell

    curl -L https://foundry.paradigm.xyz | bash
    PATH=~/.foundry/bin:$PATH
    foundryup  # Needs to be in path, or installation fails

This will install `foundryup`, `anvil` at `~/.foundry/bin` and adds the folder to your shell rc file `PATH`.

"""

import logging
import sys
import time
import warnings
from dataclasses import dataclass
from subprocess import DEVNULL, PIPE
from typing import Dict, List, Optional, Union

import psutil
import requests
from psutil import NoSuchProcess

from eth_defi.utils import is_localhost_port_listening
from eth_typing import HexAddress
from requests.exceptions import ConnectionError as RequestsConnectionError
from web3 import Web3, HTTPProvider


logger = logging.getLogger(__name__)



class InvalidArgumentWarning(Exception):
    """Lifted from Brownie. """


class RPCRequestError(Exception):
    """Lifted from Brownie. """


CLI_FLAGS = {
    "port": "--port",
    "host": "--host",
    "fork": "--fork-url",
    "fork_block": "--fork-block-number",
    "chain_id": "--chain-id",
    "default_balance": "--balance",
    "gas_limit": "--gas-limit",
}


def _launch(cmd: str, **kwargs: Dict) -> None:
    """Launches the RPC client.

    Args:
        cmd: command string to execute as subprocess"""
    if sys.platform == "win32" and not cmd.split(" ")[0].endswith(".cmd"):
        if " " in cmd:
            cmd = cmd.replace(" ", ".cmd ", 1)
        else:
            cmd += ".cmd"
    cmd_list = cmd.split(" ")
    for key, value in [(k, v) for k, v in kwargs.items() if v]:
        try:
            cmd_list.extend([CLI_FLAGS[key], str(value)])
        except KeyError:
            warnings.warn(
                f"Ignoring invalid commandline setting for anvil: "
                f'"{key}" with value "{value}".',
                InvalidArgumentWarning,
            )
    print(f"\nLaunching '{' '.join(cmd_list)}'...")
    out = DEVNULL if sys.platform == "win32" else PIPE

    return psutil.Popen(cmd_list, stdin=DEVNULL, stdout=out, stderr=out)


def on_connection() -> None:
    # set gas limit to the same as the forked network
    gas_limit = web3.eth.get_block("latest").gasLimit
    web3.provider.make_request("evm_setBlockGasLimit", [hex(gas_limit)])  # type: ignore


def _request(method: str, args: List) -> int:
    try:
        response = web3.provider.make_request(method, args)  # type: ignore
        if "result" in response:
            return response["result"]
    except (AttributeError, RequestsConnectionError):
        raise RPCRequestError("Web3 is not connected.")
    raise RPCRequestError(response["error"]["message"])


def sleep(seconds: int) -> int:
    _request("evm_increaseTime", [hex(seconds)])
    return seconds


def mine(timestamp: Optional[int] = None) -> None:
    if timestamp:
        _request("evm_setNextBlockTimestamp", [timestamp])
    _request("evm_mine", [1])


def snapshot() -> int:
    return _request("evm_snapshot", [])


def revert(snapshot_id: int) -> None:
    _request("evm_revert", [snapshot_id])


def unlock_account(address: str) -> None:
    web3.provider.make_request("anvil_impersonateAccount", [address])  # type: ignore


@dataclass
class AnvilLaunch:
    """Control Anvil processes launched on background.

    Comes with a helpful :py:meth:`close` method when it is time to put Anvil rest.
    """

    #: Which port was bound by the Anvil
    port: int

    #: Used command-line to spin up anvil
    cmd: List[str]

    #: Where does Anvil listen to JSON-RPC
    json_rpc_url: str

    #: UNIX process that we opened
    process: psutil.Popen

    def close(self, verbose=False, block=True, block_timeout=30):
        """Kill the anvil process.

        Anvil is pretty hard to kill, so keep killing it until it dies and the port is free again.

        :param block: Block the execution until Anvil has terminated
        :param block_timeout: How long we give for Anvil to clean up after itself
        :param verbose: If set, dump anything in Anvil stdout to the Python logging using level `INFO`.
        """

        process = self.process
        if verbose:
            # TODO: This does not seem to work on macOS,
            # but is fine on Ubuntu on Github CI
            logger.info("Dumping Anvil output")
            if process.poll() is not None:
                output = process.communicate()[0].decode("utf-8")
                for line in output.split("\n"):
                    logger.info(line)

        # process.terminate()
        # Hahahahah, this is Anvil, do you think terminate signal is enough
        try:
            process.kill()
        except NoSuchProcess:
            raise AssertionError("Anvil died on its own :(")

        if block:
            deadline = time.time() + 30
            while time.time() < deadline:
                if not is_localhost_port_listening(self.port):
                    # Port released, assume Anvil is gone
                    return

            raise AssertionError(f"Could not terminate Anvil in {block_timeout} seconds")


def fork_network_anvil(
    json_rpc_url: str,
    unlocked_addresses: List[Union[HexAddress, str]] = [],
    cmd="anvil",
    port=19999,
    block_time=0,
    quiet=False,
    launch_wait_seconds=20.0,
) -> AnvilLaunch:
    """Creates the Anvil "fork" of given JSON-RPC endpoint.

    Forking a mainnet is common way to test against live deployments.
    This function invokes `anvil` command and tells it to fork a given JSON-RPC endpoint.

    A subprocess is started on the background. To stop this process, call :py:meth:`eth_defi.Anvil.AnvilLaunch.close`.
    This function waits `launch_wait_seconds` in order to `anvil` process to start
    and complete the chain fork.

    .. note ::

        Currently only supports HTTP JSON-RPC connections.

    .. warning ::

        Forking a network with anvil is a slow process. It is recommended
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
        def Anvil_bnb_chain_fork(large_busd_holder) -> str:
            # Create a testable fork of live BNB chain.
            mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
            launch = fork_network(
                mainnet_rpc,
                unlocked_addresses=[large_busd_holder])
            yield launch.json_rpc_url
            # Wind down Anvil process after the test is complete
            launch.close()


        @pytest.fixture
        def web3(Anvil_bnb_chain_fork: str):
            # Set up a local unit testing blockchain
            return Web3(HTTPProvider(Anvil_bnb_chain_fork))


        def test_mainnet_fork_transfer_busd(web3: Web3, large_busd_holder: HexAddress, user_1: LocalAccount):

            # BUSD deployment on BNB chain
            # https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56
            busd_details = fetch_erc20_details(web3, "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56")
            busd = busd_details.contract

            # Transfer 500 BUSD to the user 1
            tx_hash = busd.functions.transfer(user_1.address, 500*10**18).transact({"from": large_busd_holder})

            # Because Anvil has instamine turned on by default, we do not need to wait for the transaction
            receipt = web3.eth.get_transaction_receipt(tx_hash)
            assert receipt.status == 1, "BUSD transfer reverted"

            assert busd.functions.balanceOf(user_1.address).call() == 500*10**18

    `See the full example in tests source code <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_Anvil.py>`_.

    Polygon needs to set a specific EVM version:

    .. code-block:: python

            mainnet_rpc = os.environ["POLYGON_JSON_RPC"]
            launch = fork_network(mainnet_rpc, evm_version="istanbul")

    If `anvil` refuses to terminate properly, you can kill a process by a port with:

    .. code-block:: shell

        # Kill any process listening to localhost:19999
        kill -SIGKILL $(lsof -ti:19999)

    This function uses Python logging subsystem. If you want to see error/info/debug logs with `pytest` you can do:

    .. code-block:: shell

        pytest --log-cli-level=debug

    For public JSON-RPC endpoints check

    - `BNB chain documentation <https://docs.binance.org/smart-chain/developer/rpc.html>`_

    - `ethereumnodes.com <https://ethereumnodes.com/>`_

    :param cmd: Override `anvil` command. If not given we look up from `PATH`.
    :param json_rpc_url: HTTP JSON-RPC URL of the network we want to fork
    :param unlocked_addresses: List of addresses of which ownership we take to allow test code to transact as them
    :param port: Localhost port we bind for Anvil JSON-RPC
    :param launch_wait_seconds: How long we wait anvil to start until giving up
    :param evm_version: "london" for the default hard fork
    :param block_time:
        How long Anvil takes to mine a block. Default is zero and any RPC transaction
        will immediately return with the transaction inclusion.
        Set to `1` so that you can poll the transaction as you would do with
        a live JSON-RPC node.
    :param quiet:
        Disable extensive logging. If there is a lot of Anvil logging it seems to crash
        on Github CI.

    """

    assert not is_localhost_port_listening(port), f"localhost port {port} occupied - you might have a zombie Anvil process around"

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

    # Wait until Anvil is responsive
    timeout = time.time() + launch_wait_seconds
    current_block = None

    # Use short 1.0s HTTP read timeout here - otherwise requests will wa-it > 10s if something is wrong
    web3 = Web3(HTTPProvider(url, request_kwargs={"timeout": 1.0}))
    while time.time() < timeout:
        if process.poll() is not None:
            output = process.communicate()[0].decode("utf-8")
            for line in output.split("\n"):
                logger.error(line)
            raise AssertionError(f"anvil died on launch, used command was {final_cmd}")

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
            raise AssertionError(f"anvil died on launch, used command was {final_cmd}")

        logger.error("Could not read the latest block from anvil within %f seconds", launch_wait_seconds)
        raise AssertionError(f"Could not connect to anvil {cmd}: at {url}")

    chain_id = web3.eth.chain_id

    # Use f-string for a thousand separator formatting
    logger.info(f"anvil forked network %d, the current block is {current_block:,}, Anvil JSON-RPC is %s", chain_id, url)

    return AnvilLaunch(port, final_cmd, url, process)
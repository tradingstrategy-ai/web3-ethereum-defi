"""Mock Aave v3 deployment."""
import logging

import pytest
from eth_tester import EthereumTester
from web3 import EthereumTesterProvider, HTTPProvider, Web3

from eth_defi.aave_v3.deployment import deploy_aave_v3
from eth_defi.anvil import AnvilLaunch, launch_anvil
from eth_defi.chain import install_chain_middleware


@pytest.fixture()
def anvil(request: pytest.FixtureRequest) -> AnvilLaunch:
    """Launch Anvil for the test backend.

    Run tests as `pytest --log-cli-level=info` to see Anvil console output created during the test,
    to debug any issues with Anvil itself.

    By default, Anvil is in `automining mode <https://book.getfoundry.sh/reference/anvil/>`__
    and creates a new block for each new transaction.

    .. note ::

        It could be possible to have a persitent Anvil instance over different tests with
        `fixture(scope="module")`. However we have spotted some hangs in Anvil
        (HTTP read timeout) and this is currently cured by letting Anvil reset itself.
    """

    # Peak into pytest logging level to help with Anvil output
    log_cli_level = request.config.getoption("--log-cli-level")
    log_level = None
    if log_cli_level:
        log_cli_level = logging.getLevelName(log_cli_level.upper())
        if log_cli_level <= logging.INFO:
            log_level = log_cli_level

    # London hardfork will enable EIP-1559 style gas fees
    anvil = launch_anvil(
        hardfork="london",
        gas_limit=50_000_000,  # Max 5M gas per block, or per transaction in test automining
        port=20001,
    )
    try:
        # Make the initial snapshot ("zero state") to which we revert between tests
        # web3 = Web3(HTTPProvider(anvil.json_rpc_url))
        # snapshot_id = make_anvil_custom_rpc_request(web3, "evm_snapshot")
        # assert snapshot_id == "0x0"
        yield anvil
    finally:
        anvil.close(log_level=log_level)


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.

    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(HTTPProvider(anvil.json_rpc_url, request_kwargs={"timeout": 2}))

    # Get rid of attributeddict slow down
    web3.middleware_onion.clear()

    install_chain_middleware(web3)

    return web3


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account"""
    return web3.eth.accounts[0]


def test_deploy_aave_v3(web3: Web3, deployer: str):
    """Deploy mock Aave v3."""
    deployment = deploy_aave_v3(web3, deployer)
    pool = deployment.pool

    assert pool.functions.POOL_REVISION().call() == 1

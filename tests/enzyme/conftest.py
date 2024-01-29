"""Enzyme deployment fixtures.

- Common fixtures used in all Enzyme based tests

- We need to set up a lot of stuff to ramp up Enzyme

"""

import logging

import pytest
from eth_typing import HexAddress
from pytest import FixtureRequest
from web3 import EthereumTesterProvider, HTTPProvider, Web3
from web3.contract import Contract

from eth_defi.chain import install_chain_middleware
from eth_defi.deploy import deploy_contract
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, create_token, fetch_erc20_details, reset_default_token_cache
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.deployment import (
    UniswapV2Deployment,
    deploy_trading_pair,
    deploy_uniswap_v2_like,
)
from eth_defi.usdc.deployment import deploy_fiat_token

logger = logging.getLogger(__name__)


@pytest.fixture()
def anvil(request: FixtureRequest) -> AnvilLaunch:
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
    anvil = launch_anvil()
    try:
        # Make the initial snapshot ("zero state") to which we revert between tests
        # web3 = Web3(HTTPProvider(anvil.json_rpc_url))
        # snapshot_id = make_anvil_custom_rpc_request(web3, "evm_snapshot")
        # assert snapshot_id == "0x0"
        logger.info("Anvil launched at %s", anvil.json_rpc_url)
        yield anvil
    finally:
        anvil.close(log_level=log_level)


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.

    Also perform the Anvil state reset for each test.
    """

    # We have tests mixing USDC with 6 an 18 decimals
    reset_default_token_cache()

    web3 = create_multi_provider_web3(anvil.json_rpc_url)
    return web3


@pytest.fixture()
def deployer(web3) -> HexAddress:
    """Deployer account.

    - This account will deploy all smart contracts

    - Starts with 10,000 ETH
    """
    return web3.eth.accounts[0]


@pytest.fixture()
def uniswap_v2(web3: Web3, deployer: HexAddress) -> UniswapV2Deployment:
    """Deploy Uniswap, WETH token."""
    assert web3.eth.get_balance(deployer) > 0
    deployment = deploy_uniswap_v2_like(web3, deployer, give_weth=500)  # Will also deploy WETH9 and give the deployer this many WETH tokens
    return deployment


@pytest.fixture()
def user_1(web3) -> HexAddress:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[1]


@pytest.fixture()
def user_2(web3) -> HexAddress:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[2]


@pytest.fixture()
def user_3(web3) -> HexAddress:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[3]


@pytest.fixture
def weth(uniswap_v2):
    return uniswap_v2.weth


@pytest.fixture()
def usdc_token(web3, deployer) -> TokenDetails:
    return deploy_fiat_token(web3, deployer)


@pytest.fixture()
def usdc(usdc_token) -> Contract:
    return usdc_token.contract


@pytest.fixture()
def weth_token(web3, weth) -> TokenDetails:
    return fetch_erc20_details(web3, weth.address)


@pytest.fixture()
def mln(web3, deployer) -> Contract:
    """Mock MLN token."""
    token = create_token(web3, deployer, "Melon", "MLN", 5_000_000 * 10**18)
    return token


@pytest.fixture()
def mln_token(web3, mln) -> TokenDetails:
    return fetch_erc20_details(web3, mln.address)


@pytest.fixture()
def weth_usdc_pair(web3, deployer, uniswap_v2, usdc, weth) -> Contract:
    """Create Uniswap v2 pool for WETH-USDC.

    - Add 200k initial liquidity at 1600 ETH/USDC
    """

    deposit = 200_000  # USD

    pair = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        usdc,
        weth,
        deposit * 10**6,
        (deposit // 1600) * 10**18,
    )

    return pair


@pytest.fixture()
def mln_usdc_pair(web3, deployer, uniswap_v2, usdc, mln) -> Contract:
    """mln-usd for 200k USD at $200 per token"""
    deposit = 200_000  # USD
    pair = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        usdc,
        mln,
        deposit * 10**6,
        (deposit // 200) * 10**18,
    )
    return pair


@pytest.fixture()
def weth_usd_mock_chainlink_aggregator(web3, deployer) -> Contract:
    """Fake ETH/USDC Chainlink price feed.

    Start with 1 ETH = 1600 USD.
    """
    aggregator = deploy_contract(
        web3,
        "MockChainlinkAggregator.json",
        deployer,
    )
    tx_hash = aggregator.functions.setValue(1600 * 10**8).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return aggregator


@pytest.fixture()
def usdc_usd_mock_chainlink_aggregator(web3, deployer) -> Contract:
    """Fake ETH/USDC Chainlink price feed.

    Start with 1 USDC = 1 USD.
    """
    aggregator = deploy_contract(
        web3,
        "MockChainlinkAggregator.json",
        deployer,
    )
    tx_hash = aggregator.functions.setValue(1 * 10**8).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return aggregator


@pytest.fixture()
def mln_usd_mock_chainlink_aggregator(web3, deployer) -> Contract:
    """Fake ETH/USDC Chainlink price feed.

    Start with 1 ETH = 1600 USD.
    """
    aggregator = deploy_contract(
        web3,
        "MockChainlinkAggregator.json",
        deployer,
    )
    tx_hash = aggregator.functions.setValue(200 * 10**8).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return aggregator

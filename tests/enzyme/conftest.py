"""Enzyme deployment fixtures.

- Common fixtures used in all Enzyme based tests

- We need to set up a lot of stuff to ramp up Enzyme

"""

import pytest

from eth_typing import HexAddress
from web3 import EthereumTesterProvider, Web3, HTTPProvider
from web3.contract import Contract

from eth_defi.anvil import AnvilLaunch, launch_anvil, make_anvil_custom_rpc_request
from eth_defi.deploy import deploy_contract
from eth_defi.token import create_token
from eth_defi.uniswap_v2.deployment import deploy_uniswap_v2_like, UniswapV2Deployment, deploy_trading_pair
from eth_defi.uniswap_v2.utils import sort_tokens


@pytest.fixture(scope="session")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend.

    Launch Anvil only once per pytest run, call reset between.

    Limitations

    - `Does not support stack traces <https://github.com/foundry-rs/foundry/issues/3558>`__

    - Run tests as `pytest --log-cli-level=debug` to see Anvil console output created during the test
    """

    # London hardfork will enable EIP-1559 style gas fees
    anvil = launch_anvil(
        hardfork="london",
        gas_limit=15_000_000,  # Max 5M gas per block, or per transaction in test automining
        port=20001,
    )
    try:

        # Make the initial snapshot ("zero state") to which we revert between tests
        web3 = Web3(HTTPProvider(anvil.json_rpc_url))
        snapshot_id = make_anvil_custom_rpc_request(web3, "evm_snapshot")
        assert snapshot_id == "0x0"
        yield anvil
    finally:
        anvil.close()


@pytest.fixture
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.

    Also perform the Anvil state reset for each test.
    """
    #tester = EthereumTesterProvider()
    # web3 = Web3(tester)
    web3 = Web3(HTTPProvider(anvil.json_rpc_url))
    # snapshot_id = "0x0"
    # make_anvil_custom_rpc_request(web3, "evm_revert", [snapshot_id])
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
    deployment = deploy_uniswap_v2_like(
        web3,
        deployer,
        give_weth=500  # Will also deploy WETH9 and give the deployer this many WETH tokens
    )
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
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    All initial start goes to `deployer`
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6)
    return token


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
        deposit*10**6,
        (deposit//1600)*10**18,
    )

    return pair


@pytest.fixture()
def mln(web3, deployer) -> Contract:
    """Mock MLN token.
    """
    token = create_token(web3, deployer, "Melon", "MLN", 5_000_000 * 10**18)
    return token


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
    aggregator.functions.setValue(1600 * 10**18).transact({"from": deployer})
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
    aggregator.functions.setValue(1 * 10**6).transact({"from": deployer})
    return aggregator



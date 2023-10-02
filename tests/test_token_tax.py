"""Token tax module test examples on the BSC Chain for the ELEPHANT token

To run tests in this module:

.. code-block:: shell

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_token_tax

"""
import logging
import os
import shutil

import flaky
import pytest
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.chain import install_chain_middleware, install_retry_middleware
from eth_defi.token import fetch_erc20_details
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, fetch_deployment
from eth_defi.uniswap_v2.token_tax import (
    TokenTaxInfo,
    TransferFromError,
    estimate_token_taxes,
)

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("BNB_CHAIN_JSON_RPC") is None) or (shutil.which("anvil") is None),
    reason="Set BNB_CHAIN_JSON_RPC env install anvil command to run these tests",
)


@pytest.fixture(scope="module")
def large_busd_holder() -> HexAddress:
    """A random account picked from BNB Smart chain that holds a lot of BUSD.

    This account is unlocked on Ganache, so you have access to good BUSD stash.

    `To find large holder accounts, use bscscan <https://bscscan.com/token/0xe9e7cea3dedca5984780bafc599bd69add087d56#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"))


@pytest.fixture()
def anvil_bnb_chain_fork(request, large_busd_holder) -> str:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
    launch = fork_network_anvil(
        mainnet_rpc,
        unlocked_addresses=[large_busd_holder],
    )
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture
def web3(anvil_bnb_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_bnb_chain_fork))
    install_chain_middleware(web3)
    install_retry_middleware(web3)
    return web3


@pytest.fixture
def seller(web3: Web3) -> HexAddress:
    """return a random account as seller"""
    return web3.eth.accounts[5]


@pytest.fixture(scope="module")
def sushiswap_factory_v2() -> HexAddress:
    """returns the uniswapfactoryV2 address for sushiswap on bsc"""
    return HexAddress(HexStr("0xc35DADB65012eC5796536bD9864eD8773aBc74C4"))


@pytest.fixture(scope="module")
def pancakeswap_factory_v2() -> HexAddress:
    """returns the uniswapfactoryV2 address for pancakeswap on bsc"""
    return HexAddress(HexStr("0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"))


@pytest.fixture(scope="module")
def pancake_router() -> HexAddress:
    """returns the uniswaprouterV2 address for pancakeswap in bsc"""
    return HexAddress(HexStr("0x10ED43C718714eb63d5aA57B78B54704E256024E"))


@pytest.fixture(scope="module")
def pancake_code_hash() -> str:
    """The init code hash for pancakeswap. needed while fetching deployment"""
    return "0x00fb7f630766e6a796048ea87d01acd3068e8ff67d078148a3fa3f4a84f69bd5"


@pytest.fixture(scope="module")
def elephant() -> HexAddress:
    """return hex address of the elephant token"""
    return HexAddress(HexStr("0xE283D0e3B8c102BAdF5E8166B73E02D96d92F688"))


@pytest.fixture(scope="module")
def busd() -> HexAddress:
    """return hex address of busd token"""
    return HexAddress(HexStr("0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"))


@pytest.fixture
def uniswap(web3: Web3, pancakeswap_factory_v2: HexAddress, pancake_router: HexAddress, pancake_code_hash: str) -> UniswapV2Deployment:
    """returns an instance of the pancakeswap router & factory deployment on bsc"""
    return fetch_deployment(web3, pancakeswap_factory_v2, pancake_router, pancake_code_hash)


# Flaky because uses live node
@flaky.flaky(max_runs=5)
def test_token_tax(uniswap: UniswapV2Deployment, large_busd_holder: HexAddress, seller: HexAddress, elephant: HexAddress, busd: HexAddress):
    expected_elephant_tax_percent: float = 0.1
    buy_amount: float = 1

    busd_token = fetch_erc20_details(uniswap.web3, busd)
    assert busd_token.contract.functions.balanceOf(large_busd_holder).call() > 1_000 * 10**18, "Not enough BUSD to perform the test"

    token_tax_info: TokenTaxInfo = estimate_token_taxes(uniswap, elephant, busd, large_busd_holder, seller, buy_amount)

    # asserting if the elephant tax is close to 10% or not
    assert token_tax_info.buy_tax == pytest.approx(expected_elephant_tax_percent, rel=1e-4)
    assert token_tax_info.transfer_tax == pytest.approx(expected_elephant_tax_percent, rel=1e-4)
    assert token_tax_info.sell_tax == pytest.approx(expected_elephant_tax_percent, rel=1e-4)


@flaky.flaky(max_runs=5)
def test_not_enough_tokens_to_buy(uniswap: UniswapV2Deployment, large_busd_holder: HexAddress, seller: HexAddress, elephant: HexAddress, busd: HexAddress):
    """There are not enough tokens to buy"""
    buy_amount: float = 1e30
    with pytest.raises(TransferFromError):
        estimate_token_taxes(uniswap, elephant, busd, large_busd_holder, seller, buy_amount)

"""Token taxed trades are correctly analysed."""

import os
from decimal import Decimal

import pytest
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault, LagoonAutomatedDeployment
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.trade import TradeSuccess
from eth_defi.uniswap_v2.analysis import analyse_trade_by_hash
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment, UniswapV2Deployment
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection
from eth_defi.vault.base import VaultSpec

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def usdc_holder() -> HexAddress:
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"


@pytest.fixture()
def anvil_base_fork(request, usdc_holder) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    fork_block_number = 29192700
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[usdc_holder,],
        fork_block_number=fork_block_number,
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    """Create a web3 connector.

    - By default use Anvil forked Base

    - Eanble Tenderly testnet with `JSON_RPC_TENDERLY` to debug
      otherwise impossible to debug Gnosis Safe transactions
    """

    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
    else:
        web3 = create_multi_provider_web3(
            anvil_base_fork.json_rpc_url,
            default_http_timeout=(3, 250.0),  # multicall slow, so allow improved timeout
        )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def base_usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    )


@pytest.fixture()
def base_weth(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x4200000000000000000000000000000000000006",
    )


@pytest.fixture()
def base_eai(web3) -> TokenDetails:
    """A taxed token.

    2% buy / 3% sell.

    """
    return fetch_erc20_details(
        web3,
        "0x6797b6244fa75f2e78cdffc3a4eb169332b730cc",
    )


@pytest.fixture()
def hot_wallet_user(web3, base_usdc, usdc_holder) -> HotWallet:
    """A test account with USDC balance."""

    hw = HotWallet.create_for_testing(
        web3,
        test_account_n=1,
        eth_amount=10
    )
    hw.sync_nonce(web3)

    # give hot wallet some native token
    web3.eth.send_transaction(
        {
            "from": web3.eth.accounts[9],
            "to": hw.address,
            "value": 1 * 10**18,
        }
    )

    # Top up with 999 USDC
    tx_hash = base_usdc.contract.functions.transfer(hw.address, 999 * 10**6).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    web3.middleware_onion.add(construct_sign_and_send_raw_middleware_anvil(hw.account))

    return hw

@pytest.fixture()
def random_receiver(web3) -> HexAddress:
    return web3.eth.accounts[0]


@pytest.fixture()
def uniswap_v2(web3) -> UniswapV2Deployment:
    """Uniswap V2 on Base"""
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


def test_analyse_taxed_buy(
    web3,
    uniswap_v2: UniswapV2Deployment,
    base_usdc: TokenDetails,
    base_eai: TokenDetails,
    base_weth: TokenDetails,
    hot_wallet_user: HotWallet,
):
    """Analyse a taxed buy transaction.

    - Build three-legged trade USDC -> WETH -> EAI
    - Analyse result
    - EAI has 2% buy tax, 3% sell tax
    """

    raw_amount = 500 * 10**6
    tx_hash = base_usdc.approve(uniswap_v2.router.address, Decimal(500)).transact({"from": hot_wallet_user.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # path = [
    #    base_usdc.address,
    #    base_weth.address,
    #    base_eai.address
    #]

    tx_hash = swap_with_slippage_protection(
        uniswap_v2_deployment=uniswap_v2,
        recipient_address=hot_wallet_user.address,
        base_token=base_eai.contract,
        quote_token=base_usdc.contract,
        intermediate_token=base_weth.contract,
        amount_in=raw_amount,
    ).transact({"from": hot_wallet_user.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    analysis = analyse_trade_by_hash(
        web3,
        uniswap_v2,
        tx_hash,
    )
    assert isinstance(analysis, TradeSuccess)
    assert analysis.untaxed_amount_out != analysis.amount_out

    expected_balance = analysis.amount_out
    actual_balance = base_eai.contract.functions.balanceOf(hot_wallet_user.address).call()
    diff = (expected_balance - actual_balance) / actual_balance
    assert actual_balance == pytest.approx(expected_balance), f"Expected {expected_balance} EAI, got {actual_balance}, diff: {diff:.2%}"

    expected_balance = analysis.amount_out
    untaxed_balance = analysis.untaxed_amount_out
    diff = (expected_balance - untaxed_balance) / untaxed_balance
    assert diff == pytest.approx(-0.02), f"Expected {expected_balance} EAI, got {actual_balance}, diff: {diff:.2%}"

    assert analysis.get_tax() == pytest.appr(-0.02)


@pytest.mark.skip(reason="EagleAI is not a fixed tax, but a scam token")
def test_analyse_taxed_sell(
    web3,
    uniswap_v2: UniswapV2Deployment,
    base_usdc: TokenDetails,
    base_eai: TokenDetails,
    base_weth: TokenDetails,
    hot_wallet_user: HotWallet,
    random_receiver: HexAddress,
):
    """Analyse a taxed buy transaction.

    - Sell tax
    """

    #
    # Buy tokens to test sell
    #

    raw_amount = 500 * 10**6
    tx_hash = base_usdc.approve(uniswap_v2.router.address, Decimal(500)).transact({"from": hot_wallet_user.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = swap_with_slippage_protection(
        uniswap_v2_deployment=uniswap_v2,
        recipient_address=hot_wallet_user.address,
        base_token=base_eai.contract,
        quote_token=base_usdc.contract,
        intermediate_token=base_weth.contract,
        amount_in=raw_amount,
    ).transact({"from": hot_wallet_user.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    #
    # Sell
    #

    raw_amount = base_eai.contract.functions.balanceOf(hot_wallet_user.address).call() // 2
    starting_usdc = base_usdc.contract.functions.balanceOf(hot_wallet_user.address).call()

    tx_hash = base_eai.contract.functions.approve(uniswap_v2.router.address, raw_amount).transact({"from": hot_wallet_user.address})
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = swap_with_slippage_protection(
        uniswap_v2_deployment=uniswap_v2,
        recipient_address=hot_wallet_user.address,
        base_token=base_usdc.contract,
        quote_token=base_eai.contract,
        intermediate_token=base_weth.contract,
        amount_in=raw_amount,
    ).transact({"from": hot_wallet_user.address, "gas": 2_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    analysis = analyse_trade_by_hash(
        web3,
        uniswap_v2,
        tx_hash,
    )
    assert isinstance(analysis, TradeSuccess)
    assert analysis.untaxed_amount_out != analysis.amount_out, "Tax not detected"

    expected_balance = analysis.amount_out
    actual_balance = base_eai.contract.functions.balanceOf(random_receiver).call()
    diff = (expected_balance - actual_balance) / actual_balance
    assert actual_balance == pytest.approx(expected_balance), f"Expected {expected_balance} EAI, got {actual_balance}, diff: {diff:.2%}"

    expected_balance = analysis.amount_out
    untaxed_balance = analysis.untaxed_amount_out
    diff = (expected_balance - untaxed_balance) / untaxed_balance
    assert diff == pytest.approx(-0.03), f"Expected {expected_balance} EAI, got {actual_balance}, diff: {diff:.2%}"

    assert analysis.get_tax() == pytest.approx(-0.03)

"""Lagoon swaps with Velora (ParaSwap) tests."""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.velora import (
    approve_velora,
    build_velora_swap,
)
from eth_defi.velora.api import get_augustus_swapper, get_token_transfer_proxy
from eth_defi.velora.quote import fetch_velora_quote
from eth_defi.velora.swap import fetch_velora_swap_transaction
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN, USDC_WHALE, fetch_erc20_details, BRIDGED_USDC_TOKEN, USDT_NATIVE_TOKEN, WRAPPED_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

CI = os.environ.get("CI") == "true"


@pytest.fixture()
def anvil_arbitrum_fork(request, asset_manager) -> AnvilLaunch:
    """Reset write state between tests"""

    usdc_whale = USDC_WHALE[42161]

    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=375_216_652,
        unlocked_addresses=[usdc_whale, asset_manager],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@pytest.fixture()
def topped_up_asset_manager(web3, asset_manager) -> HexAddress:
    # Topped up with some ETH
    tx_hash = web3.eth.send_transaction(
        {
            "to": asset_manager,
            "from": web3.eth.accounts[0],
            "value": 9 * 10**18,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    return asset_manager


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    usdc = fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[42161],
    )
    return usdc


@pytest.fixture()
def new_depositor(web3, usdc) -> HexAddress:
    """User with some USDC ready to deposit.

    - Start with 500 USDC
    """
    new_depositor = web3.eth.accounts[5]
    usdc_holder = USDC_WHALE[42161]
    tx_hash = usdc.transfer(new_depositor, Decimal(500)).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return new_depositor


@pytest.fixture()
def asset_manager() -> HexAddress:
    """The asset manager role."""
    return "0x0b2582E9Bf6AcE4E7f42883d4E91240551cf0947"


@pytest.fixture()
def deployer_hot_wallet(web3) -> HotWallet:
    """Manual nonce manager used for Lagoon deployment."""
    hot_wallet = HotWallet.create_for_testing(web3, eth_amount=1)
    return hot_wallet


@pytest.fixture()
def multisig_owners(web3) -> list[HexAddress]:
    """Accounts that are set as the owners of deployed Safe with vault."""
    return [web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]]


# This test requires forge deployment which requires a private key file.
# It also needs a working Anvil environment which is flaky on CI.
# The test can be run manually with the example script instead.
@pytest.mark.skipif(True, reason="Requires forge deployment with private key, run manually via example script")
def test_lagoon_velora(
    web3: Web3,
    usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    deployer_hot_wallet: HotWallet,
    multisig_owners: list[HexAddress],
    new_depositor: HexAddress,
):
    """Perform a USDC->USDC.e swap on Lagoon vault using Velora via TradingStrategyModuleV0."""

    #
    # 1. Deploy new Lagoon vault where Velora is whitelisted on the guard
    #

    chain_id = web3.eth.chain_id
    asset_manager = topped_up_asset_manager
    assert asset_manager.startswith("0x")
    depositor = new_depositor

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    # All Arbitrum mainstream stablecoins
    assets = [
        BRIDGED_USDC_TOKEN[chain_id],
        USDC_NATIVE_TOKEN[chain_id],
        USDT_NATIVE_TOKEN[chain_id],
    ]

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_hot_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,
        velora=True,
        from_the_scratch=False,
        use_forge=True,
        assets=assets,
    )

    # Check Velora and all assets are whitelisted
    vault = deploy_info.vault
    trading_strategy_module = vault.trading_strategy_module
    augustus = get_augustus_swapper(chain_id)
    proxy = get_token_transfer_proxy(chain_id)

    assert trading_strategy_module.functions.isAllowedVeloraSwapper(augustus).call() is True
    assert trading_strategy_module.functions.isAllowedApprovalDestination(proxy).call() is True
    for a in assets:
        assert trading_strategy_module.functions.isAllowedAsset(Web3.to_checksum_address(a)).call() is True
    assert not vault.trading_strategy_module.functions.anyAsset().call()

    #
    # 2. Fund our vault
    #

    # We need to do the initial valuation at value 0
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit 9.00 USDC into the vault
    usdc_amount = 9 * 10**6
    tx_hash = usdc.contract.functions.approve(vault.address, usdc_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, usdc_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # We need to do the initial valuation at value 0
    valuation = Decimal(0)
    bound_func = vault.post_new_valuation(valuation)
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Settle deposit queue 9 USDC -> 0 USDC
    settle_func = vault.settle_via_trading_strategy_module(valuation)
    tx_hash = settle_func.transact(
        {
            "from": asset_manager,
            "gas": 1_000_000,
        }
    )
    assert_transaction_success_with_explanation(
        web3,
        tx_hash,
        func=settle_func,
        tracing=True,
    )

    #
    # 3. Build and verify Velora swap transaction (don't execute - just verify structure)
    #

    usdc_amount_decimal = Decimal(5)
    usdce = fetch_erc20_details(web3, BRIDGED_USDC_TOKEN[chain_id])

    # Get quote from Velora
    quote = fetch_velora_quote(
        from_=vault.safe_address,
        buy_token=usdce,
        sell_token=usdc,
        amount_in=usdc_amount_decimal,
    )

    assert quote.get_sell_amount() == usdc_amount_decimal
    assert quote.get_buy_amount() > Decimal(0)

    # Build swap transaction
    swap_tx = fetch_velora_swap_transaction(
        quote=quote,
        user_address=vault.safe_address,
        slippage_bps=100,  # 1% slippage
    )

    assert swap_tx.to == augustus
    assert len(swap_tx.calldata) > 0
    assert swap_tx.min_amount_out > Decimal(0)

    # Build the approve function
    approve_func = approve_velora(
        vault=vault,
        token=usdc,
        amount=usdc_amount_decimal,
    )
    assert approve_func is not None

    # Build the swap function
    swap_func = build_velora_swap(
        vault=vault,
        buy_token=usdce,
        sell_token=usdc,
        amount_in=swap_tx.amount_in,
        min_amount_out=swap_tx.min_amount_out,
        augustus_calldata=swap_tx.calldata,
    )
    assert swap_func is not None


# Anvil is piece of crap
# ERROR tests/lagoon/test_lagoon_velora.py::test_velora_quote - AssertionError: Could not read block number from Anvil after the launch
@pytest.mark.skipif(CI, reason="Flaky on CI")
def test_velora_quote(
    web3: Web3,
):
    """See we can quote Velora order data unpacking correctly."""

    chain_id = web3.eth.chain_id
    weth = fetch_erc20_details(
        web3,
        WRAPPED_NATIVE_TOKEN[chain_id],
    )

    usdce = fetch_erc20_details(web3, BRIDGED_USDC_TOKEN[chain_id])

    amount = Decimal("0.0001")
    quote = fetch_velora_quote(
        from_="0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6",  # Dummy address
        buy_token=usdce,
        sell_token=weth,
        amount_in=amount,
    )

    quoted_data = quote.data
    assert quoted_data["srcToken"].lower() == weth.address.lower()
    assert quoted_data["destToken"].lower() == usdce.address.lower()
    assert int(quoted_data["srcAmount"]) > 0
    assert int(quoted_data["destAmount"]) > 0
    assert quoted_data["network"] == chain_id

    # Test helper methods
    assert quote.get_sell_amount() == amount
    assert quote.get_buy_amount() > Decimal(0)
    assert quote.get_price() > Decimal(0)

    # Test pformat doesn't crash
    quote.pformat()

    # Test building swap transaction
    swap_tx = fetch_velora_swap_transaction(
        quote=quote,
        user_address="0xdcc6D3A3C006bb4a10B448b1Ee750966395622c6",
        slippage_bps=250,  # 2.5% slippage
    )

    assert swap_tx.to is not None
    assert len(swap_tx.calldata) > 0
    assert swap_tx.amount_in == amount
    assert swap_tx.min_amount_out > Decimal(0)
    assert swap_tx.min_amount_out < quote.get_buy_amount()  # min out should be less due to slippage

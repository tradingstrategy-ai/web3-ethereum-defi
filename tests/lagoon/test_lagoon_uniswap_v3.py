"""Lagoon swap tests."""
from decimal import Decimal

import pytest
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_function_selector
from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.deployment import LagoonAutomatedDeployment, LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection
from eth_defi.uniswap_v3.deployment import fetch_deployment as fetch_deployment_uni_v3, UniswapV3Deployment


@pytest.fixture()
def uniswap_v3(web3) -> UniswapV3Deployment:
    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3_on_base = fetch_deployment_uni_v3(
        web3,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data["quoter_v2"],
        router_v2=deployment_data["router_v2"],
    )
    return uniswap_v3_on_base


def test_lagoon_uniswap_v3(
    web3: Web3,
    automated_lagoon_vault: LagoonAutomatedDeployment,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    topped_up_asset_manager: HexAddress,
    uniswap_v3: UniswapV3Deployment,
    deployer_hot_wallet: HotWallet,
    multisig_owners,
    new_depositor: HexAddress,
):
    """Perform a basic swap for Uniswap v3.

    - Check TradingStrategyModuleV0 is configured
    """

    chain_id = web3.eth.chain_id
    asset_manager = topped_up_asset_manager
    usdc = base_usdc
    depositor = new_depositor

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_hot_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=uniswap_v3,
        any_asset=True,
    )

    vault = deploy_info.vault
    assert vault.trading_strategy_module.functions.anyAsset().call()

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
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Settle deposit queue 9 USDC -> 0 USDC
    settle_func = vault.settle_via_trading_strategy_module()
    tx_hash = settle_func.transact({
        "from": asset_manager,
        "gas": 1_000_000,
    })
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check we have money for the swap
    swap_amount = usdc_amount // 2
    assert usdc.contract.functions.balanceOf(vault.safe_address).call() >= swap_amount

    # Approve USDC for the swap by the vault
    approve_call = usdc.contract.functions.approve(uniswap_v3.swap_router.address, swap_amount)
    moduled_tx = vault.transact_via_trading_strategy_module(approve_call)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check selector
    function = uniswap_v3.swap_router.functions.exactInput
    selector = get_function_selector(function)
    assert selector.hex() == "b858183f"  # Compare to whitelistUniswapV3Router in GuardV0Base

    # Do swap by the vault
    swap_call = swap_with_slippage_protection(
        uniswap_v3,
        recipient_address=vault.safe_address,
        base_token=base_weth.contract,
        quote_token=base_usdc.contract,
        amount_in=swap_amount,
        pool_fees=[500],
    )

    moduled_tx = vault.transact_via_trading_strategy_module(swap_call)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
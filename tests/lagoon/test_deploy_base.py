"""Deploy a new Lagoon vault on Base."""
import logging
from decimal import Decimal

import pytest
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection
from eth_defi.vault.base import TradingUniverse
from eth_defi.vault.valuation import NetAssetValueCalculator, UniswapV2Router02Quoter


@pytest.fixture()
def uniswap_v2(web3):
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


@pytest.fixture()
def deployer_local_account(web3) -> LocalAccount:
    hot_wallet = HotWallet.create_for_testing(web3, eth_amount=1)
    return hot_wallet.account


@pytest.fixture()
def multisig_owners(web3) -> list[HexAddress]:
    return [web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]]


@pytest.fixture()
def depositor(web3, base_usdc, usdc_holder) -> HexAddress:
    """Prepare depositor account with USDC.

    - Start with 999 USCC
    """
    address = web3.eth.accounts[5]
    tx_hash = base_usdc.contract.functions.transfer(address, 999 * 10**6).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return address


def test_lagoon_deploy_base_guarded_any_token(
    web3: Web3,
    uniswap_v2,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    depositor: HexAddress,
    usdc_holder: HexAddress,
    deployer_local_account,
    multisig_owners: list[str],
):
    """Deploy a new automated Lagoon vault

    Full e2e test to deploy a new Lagoon vault and do automated trades on it.

    1. Deploy a new Lagoon vault
    2. Do the initial valuation at 0
    3. Add deposits to the deposit queue
    4. Asset manager process deposits/revalue
    5. After deployment, perform a basic swap
    6. Revalue the vault now holding USDC and WETH
    7. Redeem free USDC back

    To run with Tenderly tx inspector:

    .. code-block:: shell

        JSON_RPC_TENDERLY="https://virtual.base.rpc.tenderly.co/XXXXXXXXXX" pytest -k test_lagoon_deploy_base_guarded_any_token

    This test will create ~50 transactions.
    """

    chain_id = web3.eth.chain_id
    deployer = deployer_local_account
    asset_manager = topped_up_asset_manager
    usdc = base_usdc

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=uniswap_v2,
        uniswap_v3=None,
        any_asset=True,
    )

    # We look correctly initialised, and
    # Safe it set to take the ownership
    vault = deploy_info.vault
    assert deploy_info.chain_id == 8453
    assert len(deploy_info.vault.safe.retrieve_owners()) == 4   # Multisig owners + deployer account we cannot remove
    assert deploy_info.trading_strategy_module.functions.owner().call() == deploy_info.vault.safe.address
    assert vault.safe.retrieve_modules() == [deploy_info.trading_strategy_module.address]
    assert deploy_info.is_asset_manager(asset_manager), f"Guard asset manager not set: {asset_manager}"
    assert vault.valuation_manager == asset_manager
    assert vault.underlying_token.symbol == "USDC"
    assert deploy_info.trading_strategy_module.functions.isAllowedLagoonVault(deploy_info.vault.address).call()
    assert vault.underlying_token.address == usdc.address
    assert usdc.contract.functions.allowance(vault.safe.address, vault.address).call() > 0

    pretty = deploy_info.pformat()
    assert type(pretty) == str
    logging.info("Deployment is:\n%s", pretty)

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

    # Deposit was registered
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    # TODO: Why ABI signature mismatch
    assert len(receipt["logs"]) == 2  # Transfer + Deposit

    # We see deposits in the queue
    assert vault.underlying_token.fetch_balance_of(depositor) == 990
    assert vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number) == Decimal(9)

    # We need to do the initial valuation at value 0
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert vault.fetch_share_price(web3.eth.block_number) == Decimal(0)

    # Settle deposit queue 9 USDC -> 0 USDC
    settle_func = vault.settle_via_trading_strategy_module()
    tx_hash = settle_func.transact({
        "from": asset_manager,
        "gas": 1_000_000,
    })
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number) == 0
    assert vault.underlying_token.fetch_balance_of(vault.safe_address) == 9
    assert vault.fetch_share_price(web3.eth.block_number) == 1
    assert vault.fetch_total_supply(web3.eth.block_number) == 9
    assert vault.fetch_total_assets(web3.eth.block_number) == 9

    # Check we have money for the swap
    swap_amount = usdc_amount // 2
    assert usdc.contract.functions.balanceOf(vault.safe_address).call() >= swap_amount

    # Approve USDC for the swap by tghe vault
    approve_call = usdc.contract.functions.approve(uniswap_v2.router.address, swap_amount)
    moduled_tx = vault.transact_via_trading_strategy_module(approve_call)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Do swap by the vault
    swap_call = swap_with_slippage_protection(
        uniswap_v2,
        recipient_address=vault.safe_address,
        base_token=base_weth.contract,
        quote_token=base_usdc.contract,
        amount_in=swap_amount,
    )

    moduled_tx = vault.transact_via_trading_strategy_module(swap_call)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Revalue portfolio / assets
    universe = TradingUniverse(
        spot_token_addresses={
            base_weth.address,
            base_usdc.address,
        }
    )
    portfolio = vault.fetch_portfolio(universe, web3.eth.block_number)
    assert portfolio.spot_erc20[base_usdc.address] > 1
    assert portfolio.spot_erc20[base_weth.address] > 0
    uniswap_v2_quoter_v2 = UniswapV2Router02Quoter(uniswap_v2.router)
    nav_calculator = NetAssetValueCalculator(
        web3,
        denomination_token=base_usdc,
        intermediary_tokens={base_weth.address},  # Allow DINO->WETH->USDC
        quoters={uniswap_v2_quoter_v2},
        debug=True,
    )
    valuation = nav_calculator.calculate_market_sell_nav(portfolio)

    # Post and settle new valuation
    vault.post_valuation_and_settle(valuation.get_total_equity(), asset_manager)

    # We lost some value in trading fees, the portfolio
    # value must be less than deposited 9 USDC now
    assert vault.fetch_total_assets(web3.eth.block_number) < 9

    # Withdraw shares to our wallet
    bound_func = vault.finalise_deposit(depositor)
    tx_hash = bound_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert vault.share_token.fetch_raw_balance_of(depositor) > 0

    # Start redemption process by estimating how many shares to redeem.
    # Check we have enough USDC in Safe to redeem
    untraded_usdc_amount = portfolio.spot_erc20[base_usdc.address]
    assert untraded_usdc_amount == Decimal("4.5")
    usdc_redeem_amount = Decimal("4.5")
    shares_to_redeem = vault.fetch_share_price(web3.eth.block_number) * usdc_redeem_amount
    shares_to_redeem_raw = vault.share_token.convert_to_raw(shares_to_redeem)

    assert usdc.fetch_balance_of(vault.safe_address) >= usdc_redeem_amount

    # Put in redemption request
    bound_func = vault.request_redeem(depositor, shares_to_redeem_raw)
    tx_hash = bound_func.transact({"from": depositor, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check the request is in the queue
    assert vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number) == pytest.approx(shares_to_redeem, rel=Decimal(0.05))

    # Revalue and settle the portfolio
    vault.post_valuation_and_settle(valuation.get_total_equity(), asset_manager)

    # Finalise redemption, USDC is moved to the user from the silo
    assert usdc.fetch_balance_of(depositor) == 990
    bound_func = vault.finalise_redeem(depositor)
    tx_hash = bound_func.transact({"from": depositor, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert usdc.fetch_balance_of(depositor) > 994
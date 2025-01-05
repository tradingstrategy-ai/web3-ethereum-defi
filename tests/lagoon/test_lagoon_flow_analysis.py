"""Lagoon vault deposit/redemption analysis"""

from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.lagoon.analysis import analyse_vault_flow_in_settlement
from eth_defi.lagoon.deployment import LagoonAutomatedDeployment
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection


def test_lagoon_deposit_redeem(
    web3: Web3,
    automated_lagoon_vault: LagoonAutomatedDeployment,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    new_depositor: HexAddress,
    another_new_depositor: HexAddress,
):
    """Check deposits and redemptions.

    - Uses test vault earlier deployed on Base

    - Deposit from user 1
    - Analyse
    - Redeem from user 1, deposit from user 2
    - Analyse

    When we mess around

    - Share price should never change, because we do not trade and incur PnL

    To run with Tenderly tx inspector:

    .. code-block:: shell

        JSON_RPC_TENDERLY="https://virtual.base.rpc.tenderly.co/XXXXXXXXXX" pytest -k test_lagoon_swap

    """
    vault = automated_lagoon_vault.vault
    asset_manager = topped_up_asset_manager
    depositor = Web3.to_checksum_address(new_depositor)
    another_new_depositor = Web3.to_checksum_address(another_new_depositor)
    usdc = base_usdc

    assert usdc.fetch_balance_of(new_depositor) == 500
    assert usdc.fetch_balance_of(vault.address) == pytest.approx(Decimal(0))

    # Deposit 9.00 USDC into the vault from the first user
    usdc_amount = Decimal(9.00)
    raw_usdc_amount = usdc.convert_to_raw(usdc_amount)
    tx_hash = usdc.approve(vault.address, usdc_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, raw_usdc_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert usdc.fetch_balance_of(vault.silo_address) == pytest.approx(Decimal(9))

    # Settle the first deposit
    tx_hash = vault.post_valuation_and_settle(Decimal(0), asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)

    assert analysis.chain_id == 8453
    assert analysis.tx_hash == tx_hash
    assert analysis.deposited == 9
    assert analysis.redeemed == 0
    assert analysis.shares_minted == 9
    assert analysis.shares_burned == 0
    assert analysis.pending_redemptions_shares == 0
    assert analysis.pending_redemptions_underlying == 0
    assert analysis.total_assets == 9
    assert analysis.total_supply == 9
    assert analysis.pending_redemptions_underlying == 0
    assert analysis.share_price == Decimal(1)  # No share price yet, because valuation as done for the empty vault
    assert analysis.deposit_events == 1
    assert analysis.redeem_events == 0
    assert usdc.fetch_balance_of(vault.silo_address) == pytest.approx(Decimal(0))
    assert usdc.fetch_balance_of(vault.safe_address) == pytest.approx(Decimal(9))

    # Second round:
    # - Partial redeem
    # - New deposit

    # Get shares for the first user (otherwise cannot redeem)
    bound_func = vault.finalise_deposit(depositor)
    tx_hash = bound_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit 5.00 USDC into the vault from the second user
    usdc_amount = Decimal(5.00)
    raw_usdc_amount = usdc.convert_to_raw(usdc_amount)
    tx_hash = usdc.approve(vault.address, usdc_amount).transact({"from": another_new_depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(another_new_depositor, raw_usdc_amount)
    tx_hash = deposit_func.transact({"from": another_new_depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Redeem 3 USDC for the first user
    shares_to_redeem_raw = vault.share_token.convert_to_raw(3)
    bound_func = vault.request_redeem(depositor, shares_to_redeem_raw)
    tx_hash = bound_func.transact({"from": depositor, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check the pending queues
    flow_manager = vault.get_flow_manager()
    block_number = web3.eth.block_number
    assert flow_manager.fetch_pending_deposit(block_number) == 5
    assert flow_manager.fetch_pending_redemption(block_number) == 3

    # Settle both
    tx_hash = vault.post_valuation_and_settle(Decimal(9), asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)

    # Check how the balance look like
    assert vault.share_token.contract.functions.totalSupply().call() == pytest.approx(11*10**18)
    assert vault.share_token.contract.functions.balanceOf(vault.address).call() == pytest.approx(5 * 10 ** 18) # Shares are held on the vault contract until redeem() called by yhe user
    assert vault.share_token.contract.functions.balanceOf(vault.silo_address).call() == pytest.approx(0)

    assert analysis.deposited == 5
    assert analysis.redeemed == pytest.approx(Decimal(3))
    assert analysis.shares_minted == pytest.approx(Decimal(5))
    assert analysis.shares_burned == pytest.approx(Decimal(3))
    assert analysis.pending_redemptions_shares == 0
    assert analysis.pending_redemptions_underlying == 0
    assert analysis.share_price == pytest.approx(Decimal(1))
    assert analysis.total_assets == pytest.approx(Decimal(11))  # Redeem not processed yet
    assert analysis.total_supply == pytest.approx(Decimal(11))
    assert analysis.deposit_events == 1
    assert analysis.redeem_events == 1

    # No events, 3 USDC redemption held by vault smart contract, Safe helds 11 USDC
    assert usdc.fetch_balance_of(vault.address) == pytest.approx(Decimal(3))
    assert usdc.fetch_balance_of(vault.safe_address) == pytest.approx(Decimal(11))
    assert usdc.fetch_balance_of(vault.silo_address) == pytest.approx(Decimal(0))
    tx_hash = vault.post_valuation_and_settle(Decimal(11), asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)
    assert analysis.deposited == 0
    assert analysis.redeemed == pytest.approx(Decimal(0))
    assert analysis.shares_minted == pytest.approx(Decimal(0))
    assert analysis.shares_burned == pytest.approx(Decimal(0))
    assert analysis.pending_redemptions_shares == 0
    assert analysis.pending_redemptions_underlying == 0
    assert analysis.total_assets == pytest.approx(Decimal(11))  # Redeem not processed yet
    assert analysis.total_supply == pytest.approx(Decimal(11))
    assert analysis.share_price == pytest.approx(Decimal(1))
    assert analysis.deposit_events == 0
    assert analysis.redeem_events == 0

    # Finally claim the redemption
    bound_func = vault.finalise_redeem(depositor, shares_to_redeem_raw)
    tx_hash = bound_func.transact({"from": depositor, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # 3 USDC was moved away from the vault, 5 USDC added, making total 11 USDC
    assert usdc.fetch_balance_of(vault.safe_address) == pytest.approx(Decimal(11))
    assert usdc.fetch_balance_of(vault.vault_address) == pytest.approx(Decimal(0))
    assert usdc.fetch_balance_of(vault.silo_address) == pytest.approx(Decimal(0))
    tx_hash = vault.post_valuation_and_settle(Decimal(11), asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)
    assert analysis.deposited == 0
    assert analysis.redeemed == pytest.approx(Decimal(0))
    assert analysis.shares_minted == pytest.approx(Decimal(0))
    assert analysis.shares_burned == pytest.approx(Decimal(0))
    assert analysis.pending_redemptions_shares == 0
    assert analysis.pending_redemptions_underlying == 0
    assert analysis.total_assets == pytest.approx(Decimal(11))  # Redeem not processed yet
    assert analysis.total_supply == pytest.approx(Decimal(11))
    assert analysis.share_price == pytest.approx(Decimal(1))
    assert analysis.deposit_events == 0
    assert analysis.redeem_events == 0

    # Check data exporter
    data = analysis.get_serialiable_diagnostics_data()
    assert isinstance(data, dict)


def test_lagoon_redeem_too_much(
    web3: Web3,
    automated_lagoon_vault: LagoonAutomatedDeployment,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    topped_up_asset_manager: HexAddress,
    new_depositor: HexAddress,
    uniswap_v2: UniswapV2Deployment,
):
    """Attempt to redeem more than the cash we have in the vaults."""

    vault = automated_lagoon_vault.vault
    asset_manager = topped_up_asset_manager
    depositor = Web3.to_checksum_address(new_depositor)
    usdc = base_usdc
    weth = base_weth

    assert usdc.fetch_balance_of(new_depositor) == 500
    assert usdc.fetch_balance_of(vault.address) == pytest.approx(Decimal(0))

    # Deposit 9.00 USDC into the vault from the first user
    usdc_amount = Decimal(9.00)
    raw_usdc_amount = usdc.convert_to_raw(usdc_amount)
    tx_hash = usdc.approve(vault.address, usdc_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, raw_usdc_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert usdc.fetch_balance_of(vault.silo_address) == pytest.approx(Decimal(9))

    # Settle the first deposit
    tx_hash = vault.post_valuation_and_settle(Decimal(0), asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)
    assert analysis.total_assets == pytest.approx(Decimal(9))

    # Swap 50/50 to WETH/USDC,
    # $4.5 USDC left behind
    swap_amount_raw = 4_500_000

    # Approve USDC for the swap by tghe vault
    approve_call = usdc.contract.functions.approve(uniswap_v2.router.address, swap_amount_raw)
    moduled_tx = vault.transact_via_trading_strategy_module(approve_call)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Do swap by the vault
    swap_call = swap_with_slippage_protection(
        uniswap_v2,
        recipient_address=vault.safe_address,
        base_token=base_weth.contract,
        quote_token=base_usdc.contract,
        amount_in=swap_amount_raw,
    )

    moduled_tx = vault.transact_via_trading_strategy_module(swap_call)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Vault has now ~8.975 worth of USDC, but it is held in WETH
    approx_value_after_swap = Decimal(8.9)
    tx_hash = vault.post_valuation_and_settle(approx_value_after_swap, asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)
    assert analysis.total_assets == pytest.approx(approx_value_after_swap)
    assert weth.fetch_balance_of(vault.safe_address) > 0
    assert usdc.fetch_balance_of(vault.safe_address) == pytest.approx(Decimal(4.5))

    # Make sure shares are in the wallet before redemption
    bound_func = vault.finalise_deposit(depositor)
    tx_hash = bound_func.transact({"from": depositor, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Try to redeem 8 USDC - we do not have enough cash
    shares_to_redeem_raw = vault.share_token.convert_to_raw(Decimal(8))
    bound_func = vault.request_redeem(depositor, shares_to_redeem_raw)
    tx_hash = bound_func.transact({"from": depositor, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Settle
    tx_hash = vault.post_valuation_and_settle(approx_value_after_swap, asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)

    # None of the pending redemptions proceed, as we have no cash and we can only do full redemptions
    assert analysis.pending_redemptions_underlying == pytest.approx(Decimal(8.089889458402021082247191008))
    assert analysis.pending_redemptions_shares == pytest.approx(Decimal(8))
    assert analysis.shares_burned == pytest.approx(Decimal(0))
    assert analysis.redeemed == pytest.approx(Decimal(0))

    # Swap all WETH back to USDC
    weth_raw_amount = weth.fetch_raw_balance_of(vault.safe_address)
    # Approve USDC for the swap by tghe vault
    approve_call = weth.contract.functions.approve(uniswap_v2.router.address, weth_raw_amount)
    moduled_tx = vault.transact_via_trading_strategy_module(approve_call)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Do swap by the vault
    swap_call = swap_with_slippage_protection(
        uniswap_v2,
        recipient_address=vault.safe_address,
        base_token=base_usdc.contract,
        quote_token=base_weth.contract,
        amount_in=weth_raw_amount,
    )

    moduled_tx = vault.transact_via_trading_strategy_module(swap_call)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Now we have enough cash in the vault
    assert usdc.fetch_balance_of(vault.safe_address) == pytest.approx(Decimal('8.97304'))

    # Settle
    tx_hash = vault.post_valuation_and_settle(approx_value_after_swap, asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)

    # Now the redemption went through,
    # and as it was partial, some cash was left in the vault
    assert analysis.pending_redemptions_underlying == 0
    assert analysis.pending_redemptions_shares == 0
    assert analysis.shares_burned == pytest.approx(Decimal(8))
    assert analysis.redeemed == pytest.approx(Decimal(7.911108), rel=Decimal(0.01))
    assert usdc.fetch_balance_of(vault.safe_address) == pytest.approx(Decimal(1.061932), rel=Decimal(0.01))

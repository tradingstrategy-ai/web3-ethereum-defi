from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.lagoon.analysis import analyse_vault_flow_in_settlement
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation


def test_lagoon_deposit(
    web3: Web3,
    uniswap_v2,
    lagoon_vault: LagoonVault,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    new_depositor: HexAddress,
    another_new_depositor: HexAddress,
):
    """Check deposits and redemptions.

    - Uses test vault earlier deployed on Base

    To run with Tenderly tx inspector:

    .. code-block:: shell

        JSON_RPC_TENDERLY="https://virtual.base.rpc.tenderly.co/XXXXXXXXXX" pytest -k test_lagoon_swap

    """
    vault = lagoon_vault
    asset_manager = topped_up_asset_manager
    depositor = new_depositor
    usdc = base_usdc

    assert usdc.fetch_balance_of(new_depositor) == 500

    # Deposit 9.00 USDC into the vault
    usdc_amount = Decimal(9.00)
    raw_usdc_amount = usdc.convert_to_raw(usdc_amount)
    tx_hash = usdc.approve(vault.address, usdc_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, raw_usdc_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Settle the first deposit
    valuation = 0
    tx_hash = vault.post_valuation_and_settle(0, asset_manager)
    analysis = analyse_vault_flow_in_settlement(vault, tx_hash)




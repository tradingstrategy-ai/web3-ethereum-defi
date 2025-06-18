"""ERC-4626 deposit slippage analysis."""

from decimal import Decimal
from typing import Literal

from web3.logs import DISCARD

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.trade import TradeSuccess, TradeFail


def analyse_4626_flow_transaction(
    vault: ERC4626Vault,
    tx_hash: str | bytes,
    tx_receipt: dict,
    direction: Literal["deposit", "redeem"],
    hot_wallet=True,
) -> TradeSuccess | TradeFail:
    """Analyse a ERC-4626 deposit/redeem transaction.

    Figure out

    - The success of the deposit

    - Slippage, etc.

    .. warning::

        Do not use `TradeSuccess.price` directly, as this price depends on in which order token0 and token1
        are in the pool smart contract. Use `TradeSuccess.get_human_price()` instead.

    :param tx_receipt:
        Transaction receipt

    :param hot_wallet:
        Is this a hot wallet originiated transaction or contract to contract transaction.

        We can perform additioanl checks with hot wallet transactions.

    """

    web3 = vault.web3

    if hot_wallet:
        assert tx_receipt["to"] == vault.address, f"Transaction receipt 'to' address {tx_receipt['to']} does not match vault address {vault.address}.\nVault is: {vault}"

    assert direction in ("deposit", "redeem")

    effective_gas_price = tx_receipt.get("effectiveGasPrice", 0)
    gas_used = tx_receipt["gasUsed"]

    # TODO: Unit test this code path
    # Tx reverted
    if tx_receipt["status"] != 1:
        reason = fetch_transaction_revert_reason(web3, tx_hash)
        return TradeFail(gas_used, effective_gas_price, revert_reason=reason)

    contract = vault.vault_contract

    if direction == "deposit":
        in_token_details = vault.denomination_token
        out_token_details = vault.share_token
        swap_events = contract.events.Deposit().process_receipt(tx_receipt, errors=DISCARD)
    else:
        in_token_details = vault.share_token
        out_token_details = vault.denomination_token
        swap_events = contract.events.Withdraw().process_receipt(tx_receipt, errors=DISCARD)

    # The contract deposit/redeem may trigger same event in nested contracts so we clean up here
    swap_events = [event for event in swap_events if event["address"].lower() == vault.vault_address.lower()]

    path = [in_token_details.address_lower, out_token_details.address_lower]
    amount_out_min = None

    #: TODO Get deducted fees
    lp_fee_paid = 0

    if len(swap_events) == 1:
        first_event = swap_events[0]

        if direction == "deposit":
            amount_in = first_event["args"]["assets"]
            amount_out = first_event["args"]["shares"]
        else:
            amount_in = first_event["args"]["shares"]
            amount_out = first_event["args"]["assets"]

    elif len(swap_events) == 0:
        raise AssertionError(f"No {direction} events detected for vault {vault.vault_address}: {tx_receipt}")
    else:
        raise AssertionError(f"Can handle only single event per tx, got {len(swap_events)}. Receipt: {tx_receipt}")

    assert amount_out > 0, "amount out should be negative for ERC-4626 flow event"

    amount_out_cleaned = Decimal(abs(amount_out)) / Decimal(10**out_token_details.decimals)
    amount_in_cleaned = Decimal(abs(amount_in)) / Decimal(10**in_token_details.decimals)

    price = amount_out_cleaned / amount_in_cleaned

    if direction == "deposit":
        price = Decimal(1) / price

    return TradeSuccess(
        gas_used,
        effective_gas_price,
        path,
        amount_in,
        amount_out_min,
        abs(amount_out),
        price,
        in_token_details.decimals,
        out_token_details.decimals,
        token0=in_token_details,
        token1=out_token_details,
        lp_fee_paid=lp_fee_paid,
    )

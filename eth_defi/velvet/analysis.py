"""Analyse price impact and slippage of executed Enso trades"""

from pprint import pformat

from web3 import Web3
from web3.logs import DISCARD

from eth_defi.abi import get_contract
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.token import fetch_erc20_details
from eth_defi.trade import TradeFail, TradeSuccess


def analyse_trade_by_receipt_generic(
    web3: Web3,
    tx_hash: str | bytes,
    tx_receipt: dict | None,
    intent_based=True,
) -> TradeSuccess | TradeFail:
    """Analyse of any trade based on ERC-20 transfer events.

    Figure out

    - The success of the trade

    - Actual realised price (no idea of planned price w/slippage)

    Use only ERC-20 `Transfer` event and do not peek into underlying DEX details.

    - Assume first `Transfer()` event is tokens going into trade

    - Assume last `Transfer()` event is tokens coming out of the trade

    Example:

    .. code-block:: python


        # Build tx using Velvet API
        tx_data = vault.prepare_swap_with_enso(
            token_in="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            token_out="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",
            swap_amount=1_000_000,  # 1 USDC
            slippage=slippage,
            remaining_tokens=universe.spot_token_addresses,
            swap_all=False,
            from_=vault_owner,
        )

        # Perform swap
        tx_hash = web3.eth.send_transaction(tx_data)
        assert_transaction_success_with_explanation(web3, tx_hash)

        receipt = web3.eth.get_transaction_receipt(tx_hash)

        analysis = analyse_trade_by_receipt_generic(
            web3,
            tx_hash,
            receipt,
        )

        assert isinstance(analysis, TradeSuccess)
        assert analysis.intent_based
        assert analysis.token0.symbol == "USDC"
        assert analysis.token1.symbol == "doginme"
        assert analysis.amount_in == 1 * 10**6
        assert analysis.amount_out > 0
        # https://www.coingecko.com/en/coins/doginme
        price = analysis.get_human_price(reverse_token_order=True)
        assert 0 < price < 0.01

    :return:
        TradeSuccess or TradeFail instance.

        For TradeSuccess, unknown fields we cannot figure out without DEX details are set to ``None``.
    """

    chain_id = web3.eth.chain_id

    if tx_receipt is None:
        tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
        assert tx_receipt, f"Transaction receipt for {tx_hash} not found"

    effective_gas_price = tx_receipt.get("effectiveGasPrice", 0)
    gas_used = tx_receipt["gasUsed"]

    if tx_receipt["status"] != 1:
        reason = fetch_transaction_revert_reason(web3, tx_hash)
        return TradeFail(gas_used, effective_gas_price, revert_reason=reason)

    ERC20 = get_contract(web3, "ERC20MockDecimals.json")

    transfer_events = ERC20.events.Transfer().process_receipt(tx_receipt, errors=DISCARD)

    # WTF clean up.
    # Some scam tokens generate extra Transfer events with value 0?
    transfer_event_count = len(transfer_events)
    transfer_events = [evt for evt in transfer_events if evt["args"]["value"] != 0]

    if len(transfer_events) < 2:
        tx = web3.eth.get_transaction(tx_hash)
        return TradeFail(gas_used, effective_gas_price, revert_reason=f"analyse_trade_by_receipt_generic() needs at least 2 transfer events\nGot {len(transfer_events)}, the transaction status was {tx_receipt['status']}, tx hash: {tx_hash.hex()}\nto: {tx['to']}, input: {tx['input'].hex()}\nPotential reason: to contract does not exist")

    first_transfer_event = transfer_events[0]
    last_transfer_event = transfer_events[-1]

    in_token_details = fetch_erc20_details(web3, first_transfer_event["address"], chain_id=chain_id)
    out_token_details = fetch_erc20_details(web3, last_transfer_event["address"], chain_id=chain_id)

    amount_in = first_transfer_event["args"]["value"]
    amount_out_min = None
    amount_out = last_transfer_event["args"]["value"]

    amount_out_cleaned = out_token_details.convert_to_decimals(amount_out)
    amount_in_cleaned = in_token_details.convert_to_decimals(amount_in)

    assert amount_in_cleaned > 0, f"Swap amount in detected to be zero. Tx hash: {tx_hash.hex()}, amount_in_cleaned: {amount_in_cleaned}, events:\n{pformat(transfer_events)}"

    price = amount_out_cleaned / amount_in_cleaned
    lp_fee_paid = None

    path = [in_token_details.address, out_token_details.address]

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
        intent_based=intent_based,
        transfer_event_count=transfer_event_count,
    )

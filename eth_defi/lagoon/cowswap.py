"""Cow swap support for Lagoon vaults."""
from decimal import Decimal

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.token import TokenDetails


def presign_cowswap(
    vault: LagoonVault,
    buy_token: TokenDetails,
    sell_token: TokenDetails,
    amount_in: Decimal,
    min_amount_out: Decimal,
):
    """Construct a pre-signed CowSwap order for the offchain order book to execute using TradingStrategyModuleV0."""

    assert isinstance(vault, LagoonVault), f"Not a Lagoon vault: {type(vault)}"
    assert isinstance(buy_token, TokenDetails), f"Not a TokenDetails: {type(buy_token)}"
    assert isinstance(sell_token, TokenDetails), f"Not a TokenDetails: {type(sell_token)}"
    assert isinstance(amount_in, Decimal), f"Not a Decimal: {type(amount_in)}"
    assert isinstance(min_amount_out, Decimal), f"Not a Decimal: {type(min_amount_out)}"

    amount_in_raw = buy_token.convert_to_raw(amount_in)
    min_amount_out_raw = sell_token.convert_to_raw(min_amount_out)

    trda

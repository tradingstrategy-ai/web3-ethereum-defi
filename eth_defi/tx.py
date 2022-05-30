"""Tranaction decoding."""

from typing import Union

from eth_account._utils.typed_transactions import TypedTransaction
from hexbytes import HexBytes


def decode_signed_transaction(raw_bytes: Union[bytes, str, HexBytes]) -> TypedTransaction:
    """Decode already signed transaction.

    Reverse raw transaction bytes back to dictionary form.
    """

    if not isinstance(raw_bytes, HexBytes):
        raw_bytes = HexBytes(raw_bytes)

    return TypedTransaction.from_bytes(raw_bytes)


"""Transaction parsing and building utilities."""

from dataclasses import dataclass
from typing import Union

from eth_account._utils.legacy_transactions import Transaction
from eth_account._utils.typed_transactions import TypedTransaction
from eth_typing import HexAddress
from hexbytes import HexBytes


class DecodeFailure(Exception):
    """We could not decode transaction for a reason or another."""


def decode_signed_transaction(raw_bytes: Union[bytes, str, HexBytes]) -> dict:
    """Decode already signed transaction.

    Reverse raw transaction bytes back to dictionary form, so you can access
    its `data` field and other parameters.

    The function supports:

    - Legacy transactions

    - `EIP-2718, EIP-2930 transactions introduced in Berlin hard fork <https://twitter.com/quicknode/status/1384212705658040330>`_.

    - See `TypedTransaction source <https://github.com/ethereum/eth-account/blob/e78dfe871f4cb708fb9c842a42ca3e14697fb065/eth_account/_utils/typed_transactions.py#L112>`_
        for more documentation.

    - `EIP-2718 spec <https://eips.ethereum.org/EIPS/eip-2718>`_

    - `EIP-2930 spec <https://eips.ethereum.org/EIPS/eip-2930>`_

    Example:

    .. code-block:: python

        signed_tx = hot_wallet.sign_transaction_with_new_nonce(raw_tx)
        signed_tx_bytes = signed_tx.rawTransaction
        d = decode_signed_transaction(signed_tx_bytes)
        assert d["chainId"] == 1337
        assert d["nonce"] == 0
        assert d["data"].hex().startswith("0xa9059cbb0")  # transfer() function selector

    :param raw_bytes:
        A bunch of bytes in your favorite format.

    :raise DecodeFailure:
        If the tx bytes is something we do not know how to handle.

    :return:
        Dictionary like object containing `data`, `v`, `r`, `s`, `nonce`, `value`, `gas`, `gasPrice`.
        Some fields like `chainId`, `accessList`, `maxPriorityFeePerGas` depend on the transaction type.
    """

    if not isinstance(raw_bytes, HexBytes):
        raw_bytes = HexBytes(raw_bytes)

    try:
        # First we try EIP-2718 and this will fail we fall back to the legacy tx
        typed_tx = TypedTransaction.from_bytes(raw_bytes)
        return typed_tx.transaction.dictionary
    except ValueError:
        try:
            return Transaction.from_bytes(raw_bytes).as_dict()
        except Exception as e:
            raise DecodeFailure(f"Could not decode transaction: {raw_bytes.hex()}") from e


@dataclass(slots=True, frozen=True)
class AssetDelta:
    """Spend/incoming asset information.

    Some transaction builders, like Enzyme vaults, need to have the incoming/outgoing
    asset information associated with the transaction. This is because
    internally Enzyme needs to move assets to the adapter contract from the vault
    contract to perform the transaction.

    We use this data structure to describe what assets the transaction touches.

    See :py:mod:`eth_defi.enzyme.vault_transaction_builder` for more information.
    """

    #: The ERC-20 token for this delta.
    asset: HexAddress | str

    #: Changed amount.
    #:
    #: Negative for tokens that are going to be used for purchases in this tx, positive for incoming.
    #:
    #: Much include any slippage tolerance for trades.
    raw_amount: int

    def __post_init__(self):
        assert type(self.raw_amount) == int
        assert type(self.asset) in (HexAddress, str)
        assert self.raw_amount, "Raw amount should be specified"

    def __mul__(self, other: float | int) -> "AssetDelta":
        """Adjust asset delta by multiplier.

        E.g. multiplied by 0.99 returns :py:class:`AssetDelta` with
        raw amount reduced 1%. Uses integer flooring.

        Example:

        .. code-block:: python

            d = AssetDelta(usdc.address, 1*10**6)
            d2 = d * 0.99
            assert d2.raw_amount == int(10**6 * 0.99)
        """
        assert isinstance(other, (float, int))
        return AssetDelta(self.asset, int(self.raw_amount * other))

    def is_incoming(self) -> bool:
        """This delta describes incoming assets."""
        return self.raw_amount > 0

    def is_spending(self) -> bool:
        """This delta describes assets that we spend in the transaction."""
        return self.raw_amount < 0

    def as_json_friendly_dict(self):
        """Get the asset delta representation as JSON'nable dict.

        We need to convert large Python ints to strings.
        """
        return {"asset": str(self.asset), "raw_amount": str(self.raw_amount)}

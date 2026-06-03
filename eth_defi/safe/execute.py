"""Fix broken Safe Python SDK stuff.

The upstream ``safe_eth`` library manages transaction nonces internally
by reading from the RPC node.  On load-balanced endpoints this often
returns stale values, causing "nonce too low" errors when multiple
transactions are sent in quick succession (e.g. during multichain
vault deployment).

All functions in this module accept an optional
:class:`~eth_defi.hotwallet.HotWallet` parameter.  When provided the
nonce is allocated from the wallet's internal counter instead of
querying the RPC, keeping it in sync with other ``HotWallet``-managed
transactions in the same deployment flow.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Tuple

from eth_account import Account
from hexbytes import HexBytes
from web3.types import BlockIdentifier, Nonce, TxParams, Wei

from safe_eth.eth.ethereum_client import TxSpeed
from safe_eth.safe.safe_tx import SafeTx

from ..gas import GasPriceSuggestion, apply_gas

if TYPE_CHECKING:
    from eth_defi.hotwallet import HotWallet


logger = logging.getLogger(__name__)


def execute_safe_tx(
    self: SafeTx,
    tx_sender_private_key: str,
    tx_gas: Optional[int] = None,
    tx_gas_price: Optional[int] = None,
    tx_nonce: Optional[int] = None,
    block_identifier: Optional[BlockIdentifier] = "latest",
    eip1559_speed: Optional[TxSpeed] = None,
    gas_fee: GasPriceSuggestion = None,
    hot_wallet: HotWallet | None = None,
) -> Tuple[HexBytes, TxParams]:
    """Fixed version of ``SafeTx.execute()``.

    - See the original as :py:meth:`safe_eth.safe.safe_tx.SafeTx.execute()`

    - Handle gas fees correctly, don't fail randomly

    :param gas_fee:
        Gas fee to apply to the transaction, don't try to use broken Safe logic to get gas fee filled in,
        as it will result to broken transactions rejected by the node.

    :param hot_wallet:
        When provided, allocate the transaction nonce from the wallet's
        internal counter instead of reading from the RPC node.  This
        avoids stale-nonce errors on load-balanced endpoints.
        Takes precedence over ``tx_nonce``.
    """

    assert isinstance(self, SafeTx), f"execute_safe_tx() must be called on SafeTx instance, got {type(self)}"

    if hot_wallet is not None:
        tx_nonce = hot_wallet.allocate_nonce()

    sender_account = Account.from_key(tx_sender_private_key)
    if eip1559_speed and self.ethereum_client.is_eip1559_supported():
        tx_parameters = self.ethereum_client.set_eip1559_fees(
            {
                "from": sender_account.address,
            },
            tx_speed=eip1559_speed,
        )
    else:
        tx_parameters = {
            "from": sender_account.address,
            "gasPrice": Wei(tx_gas_price) if tx_gas_price else self.w3.eth.gas_price,
        }

    if tx_gas:
        tx_parameters["gas"] = tx_gas
    if tx_nonce is not None:
        tx_parameters["nonce"] = Nonce(tx_nonce)

    self.tx = self.w3_tx.build_transaction(tx_parameters)

    self.tx["gas"] = Wei(tx_gas or (max(self.tx["gas"] + 75000, self.recommended_gas())))

    #  Correctly apply gas estimate if given
    if gas_fee:
        logger.info(f"Using gas estimate: {gas_fee.pformat()}")
        self.tx = apply_gas(self.tx, gas_fee)
    else:
        logger.warning(f"execute_safe_tx(): No gas estimate given, SafeTx.execute() may fail with gas pricing issues on production chains")

    self.tx_hash = self.ethereum_client.send_unsigned_transaction(
        self.tx,
        private_key=sender_account.key,
        retry=False if tx_nonce is not None else True,
        block_identifier=block_identifier,
    )

    # Set signatures empty after executing the tx. `Nonce` is increased even if it fails,
    # so signatures are not valid anymore
    self.signatures = b""
    return self.tx_hash, self.tx

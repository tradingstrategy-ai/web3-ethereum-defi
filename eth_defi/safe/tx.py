"""Gnosis Safe multisignature transaction handling.

.. note ::

    This code may be unfinished and untested.
"""

import logging

from eth_typing import HexAddress
from eth_account import Account  # For handling private keys

from hexbytes import HexBytes

from safe_eth.safe import Safe
from safe_eth.safe.exceptions import CannotEstimateGas
from safe_eth.safe.safe_tx import SafeTx
from safe_eth.safe.api.transaction_service_api.transaction_service_api import TransactionServiceApi
from safe_eth.eth import EthereumClient
from safe_eth.safe.signatures import signatures_to_bytes


logger = logging.getLogger(__name__)


class SafeTxProposalError(Exception):
    """Error proposing Safe transaction"""


def propose_safe_transaction(
    safe: Safe,
    address: HexAddress | str,
    private_key: str,
    data: bytes | HexBytes,
    operation=0,
    value: int = 0,
) -> SafeTx:
    """Propose a Safe transaction for Safe UI others to sign.

    :param safe:
        The Safe instance

    :param address:
        Target contract address

    :param private_key:
        Proposer's private key

    :parma data:
        Contract call payload

    :raise SafeTxProposalError:
        If we have a problem with the transaction service

    :return:
        Proposed Safe transaction
    """
    assert isinstance(safe, Safe), f"Not safe: {safe}"
    assert type(value) is int, f"Value must be int, got {type(value)}"
    assert address.startswith("0x"), f"Address must be hex, got {address}"
    assert isinstance(data, bytes), f"Data must be bytes, got {type(data)}"
    assert private_key.startswith("0x"), f"Private key must be hex, got {private_key}"

    ethereum_client: EthereumClient = safe.ethereum_client

    # Estimate safeTxGas (use Safe helpers)
    try:
        estimated_safe_tx_gas = safe.estimate_tx_gas_with_safe(
            to=address,
            value=value,
            data=data,
            operation=operation,
        )
    except Exception as e:
        logger.error("Could not estimate gas with Safe: %s", e, exc_info=True)
        # fallback: try web3 estimate or set a conservative value
        try:
            estimated_safe_tx_gas = safe.estimate_tx_gas_with_web3(to=address, value=value, data=data)
        except CannotEstimateGas as e2:
            raise RuntimeError(f"Could not estimate gas for Safe transaction: {e2} for address {address}, data: {data.hex()}") from e

    # You also need base_gas/gas_price/etc; for simplicity, set base_gas=0 and gas_price=0 here
    safe_tx = SafeTx(
        ethereum_client,
        safe.address,
        to=address,
        value=value,
        data=data,
        operation=operation,
        safe_tx_gas=estimated_safe_tx_gas,
        base_gas=0,
        gas_price=0,
        gas_token=None,
        refund_receiver=None,
    )

    # Get the hash owners must sign
    safe_tx_hash = safe_tx.safe_tx_hash  # HexBytes
    logger.info("Proposing safeTxHash: %s", safe_tx_hash.hex())

    # Example: owner signs the hash locally (EOA)
    owner_account = Account.from_key(private_key)
    # NOTE: Safe expects an ECDSA signature over the safeTxHash (see Safe docs). We'll sign the raw hash.
    signed = owner_account.signHash(safe_tx_hash)  # returns v,r,s
    v, r, s = signed.v, signed.r, signed.s

    # Convert to the bytes format expected by safe-eth-py / tx service:
    # signatures_to_bytes expects list of (v, r, s)
    sig_bytes = signatures_to_bytes([(v, r, s)])

    # Attach signatures to the SafeTx (signatures field should be bytes of concatenated sigs)
    safe_tx.signatures = sig_bytes

    network = ethereum_client.get_network()

    tx_service = TransactionServiceApi(
        network=network,
        ethereum_client=ethereum_client,
    )

    logger.info("Posting transaction using Gnosis Safe API services %s", tx_service.ethereum_client)

    posted = tx_service.post_transaction(safe_tx)
    if not posted:
        raise SafeTxProposalError(f"Could not post Safe transaction to tx service: {safe_tx} to {tx_service.base_url}")

    return safe_tx

"""Gnosis Safe multisignature transaction handling."""

import logging

from eth_typing import HexAddress
from hexbytes import HexBytes
from eth_account import Account
from safe_eth.safe import Safe
from safe_eth.safe.safe_tx import SafeTx
from safe_eth.safe.api.transaction_service_api.transaction_service_api import TransactionServiceApi
from web3 import Web3


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
    """Create a signed Safe Transaction Service proposal without broadcasting it.

    :param safe:
        The Safe instance

    :param address:
        Target contract address

    :param private_key:
        Proposer's private key

    :param data:
        Contract call payload

    :raise SafeTxProposalError:
        If we have a problem with the transaction service

    :return:
        Proposed Safe transaction
    """
    if type(value) is not int or value < 0:
        raise ValueError(f"Value must be a non-negative integer, got {value!r}")
    if type(operation) is not int or operation not in (0, 1):
        raise ValueError(f"Operation must be Safe Call (0) or DelegateCall (1), got {operation!r}")
    if not isinstance(data, bytes):
        raise TypeError(f"Data must be bytes, got {type(data)}")
    target = Web3.to_checksum_address(address)
    owner_account = Account.from_key(private_key)
    safe_owners = {owner.lower() for owner in safe.retrieve_owners()}
    if owner_account.address.lower() not in safe_owners:
        raise SafeTxProposalError(
            f"Proposer {owner_account.address} is not an owner of Safe {safe.address}"
        )

    # A Transaction Service proposal is not an on-chain execution, so it must
    # not include a Safe refund configuration. The Safe UI estimates execution
    # gas when the owners later execute the transaction.
    safe_tx = safe.build_multisig_tx(
        to=target,
        value=value,
        data=data,
        operation=operation,
    )
    safe_tx.sign(private_key)
    logger.info("Proposing Safe transaction hash: %s", safe_tx.safe_tx_hash.hex())

    network = safe.ethereum_client.get_network()

    tx_service = TransactionServiceApi(
        network=network,
        ethereum_client=safe.ethereum_client,
    )

    logger.info("Posting Safe transaction proposal to %s", tx_service.base_url)

    posted = tx_service.post_transaction(safe_tx)
    if not posted:
        raise SafeTxProposalError(
            f"Could not post Safe transaction {safe_tx.safe_tx_hash.hex()} to {tx_service.base_url}"
        )

    return safe_tx

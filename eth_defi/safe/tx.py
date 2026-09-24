"""Gnosis Safe multisignature transaction handling."""

import logging

from eth_typing import HexAddress
from hexbytes import HexBytes
from eth_account import Account
from safe_eth.eth.ethereum_network import EthereumNetworkNotSupported
from safe_eth.safe import Safe
from safe_eth.safe.api.base_api import SafeAPIException
from safe_eth.safe.safe_tx import SafeTx
from safe_eth.safe.api.transaction_service_api.transaction_service_api import TransactionServiceApi
from safe_eth.safe.enums import SafeOperationEnum
from web3 import Web3


logger = logging.getLogger(__name__)


class SafeTxProposalError(Exception):
    """Error proposing Safe transaction"""


def propose_safe_transaction(
    safe: Safe,
    address: HexAddress | str,
    private_key: str,
    data: bytes | HexBytes,
    operation: SafeOperationEnum | int = SafeOperationEnum.CALL,
    value: int = 0,
) -> SafeTx:
    """Create a signed Safe Transaction Service proposal without broadcasting it.

    On 2026-09-19, the deployed Lighter tutorial Safe accepted and indexed a
    zero-value, state-free ``totalAssets()`` proposal while
    ``SAFE_TRANSACTION_SERVICE_API_KEY`` was unset. The key is optional, but
    Safe recommends it in production for higher rate limits and reliability.

    :param safe:
        The Safe instance

    :param address:
        Target contract address

    :param private_key:
        Proposer's private key

    :param data:
        Contract call payload

    :param operation:
        Safe Call or DelegateCall operation

    :param value:
        Native-token value to transfer with the contract call

    :raise SafeTxProposalError:
        If the configured Safe Transaction Service does not support the chain
        or rejects the proposal

    :return:
        Proposed Safe transaction

    See `Safe Transaction Service API <https://docs.safe.global/core-api/transaction-service-reference>`__.
    """
    if type(value) is not int or value < 0:
        raise ValueError(f"Value must be a non-negative integer, got {value!r}")
    if type(operation) not in (int, SafeOperationEnum) or operation not in (0, 1):
        raise ValueError(f"Operation must be Safe Call (0) or DelegateCall (1), got {operation!r}")
    if not isinstance(data, bytes):
        raise TypeError(f"Data must be bytes, got {type(data)}")
    target = Web3.to_checksum_address(address)
    owner_account = Account.from_key(private_key)
    safe_owners = {owner.lower() for owner in safe.retrieve_owners()}
    if owner_account.address.lower() not in safe_owners:
        raise SafeTxProposalError(f"Proposer {owner_account.address} is not an owner of Safe {safe.address}")

    # A Transaction Service proposal is not an on-chain execution, so it must
    # not include a Safe refund configuration. The Safe UI estimates execution
    # gas when the owners later execute the transaction.
    safe_tx = safe.build_multisig_tx(
        to=target,
        value=value,
        data=data,
        operation=int(operation),
    )
    safe_tx.sign(private_key)
    logger.info("Proposing Safe transaction hash: %s", safe_tx.safe_tx_hash.hex())

    try:
        tx_service = TransactionServiceApi(
            network=safe.ethereum_client.get_network(),
            ethereum_client=safe.ethereum_client,
        )
        logger.info("Posting Safe transaction proposal to %s", tx_service.base_url)
        tx_service.post_transaction(safe_tx)
    except (EthereumNetworkNotSupported, SafeAPIException) as e:
        raise SafeTxProposalError(f"Could not post Safe transaction {safe_tx.safe_tx_hash.hex()}: {e}") from e

    return safe_tx

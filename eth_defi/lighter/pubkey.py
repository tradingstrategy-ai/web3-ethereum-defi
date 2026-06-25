"""Lighter API-key registration (``changePubKey``) for a Safe-controlled account.

Creating a Lighter API key is two steps:

1. **Generate** the API keypair off-chain (the `lighter-python` SDK
   ``SignerClient``; no L1 private key needed). Indices 2-254 are user keys;
   0-1 are reserved for the web/mobile UI.
2. **Register** its public key with the Lighter account on L1 via
   ``ZkLighter.changePubKey(accountIndex, apiKeyIndex, pubKey)``. For a Gnosis
   Safe / Lagoon vault this is done as a **Safe transaction** — Lighter
   explicitly recommends the on-chain ``ChangePubKey`` "if you're running a
   multi-sig" (the SDK's EOA ``sign_change_api_key`` path needs a raw private
   key, which a Safe does not have).

This module covers step 2 for a Safe: validating the pubkey, encoding the
``changePubKey`` call, and building / proposing / executing the Safe transaction.

.. note::

    ``changePubKey`` is intentionally **not** part of the asset-manager guard
    whitelist (:py:mod:`eth_defi.lighter` ``LighterLib``). It is a privileged
    setup action performed by the Safe owners (governance), so it goes
    **directly through the Safe**, not the ``TradingStrategyModule``'s
    restricted ``performCall`` path. The asset-manager hot wallet cannot rotate
    trading keys.

Authoritative docs:

- Lighter API keys: https://apidocs.lighter.xyz/docs/api-keys
- ``lighter-python`` ``SignerClient.sign_change_api_key``:
  https://github.com/elliottech/lighter-python/blob/main/lighter/signer_client.py
"""

import logging

from eth_typing import HexAddress
from hexbytes import HexBytes
from safe_eth.safe import Safe
from safe_eth.safe.safe_tx import SafeTx
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

#: Required Lighter API-key public-key length in bytes.
#:
#: 5 Goldilocks field limbs of 8 bytes each. From ``ZkLighter.PUB_KEY_BYTES_SIZE``.
PUB_KEY_BYTES_SIZE = 40

#: Goldilocks field modulus (``2**64 - 2**32 + 1``).
#:
#: Each 8-byte little-endian limb of the pubkey must be strictly below this.
#: From ``ZkLighter.GOLDILOCKS_MODULUS`` (``0xffffffff00000001``).
GOLDILOCKS_MODULUS = 0xFFFFFFFF00000001

#: Maximum API-key index. From ``ZkLighter.MAX_API_KEY_INDEX``.
#:
#: Indices 2-254 are user keys; 0-1 are reserved for the web/mobile interfaces.
MAX_API_KEY_INDEX = 254


def validate_lighter_pubkey(pubkey: bytes) -> None:
    """Validate a Lighter API-key public key client-side.

    Mirrors the on-chain checks in ``ZkLighter.changePubKey`` so callers fail
    fast before submitting a transaction: the pubkey must be exactly
    :py:data:`PUB_KEY_BYTES_SIZE` bytes, each 8-byte little-endian limb must be
    strictly below :py:data:`GOLDILOCKS_MODULUS`, and it must not be all-zero.

    :param pubkey:
        The API-key public key, as produced by the Lighter SDK.

    :raises ValueError:
        If the pubkey is malformed.
    """
    if len(pubkey) != PUB_KEY_BYTES_SIZE:
        raise ValueError(f"Lighter pubkey must be {PUB_KEY_BYTES_SIZE} bytes, got {len(pubkey)}")

    all_zero = True
    for i in range(5):
        limb = int.from_bytes(pubkey[8 * i : 8 * (i + 1)], "little")
        if limb >= GOLDILOCKS_MODULUS:
            raise ValueError(f"Lighter pubkey limb {i} ({limb}) >= Goldilocks modulus")
        if limb != 0:
            all_zero = False

    if all_zero:
        raise ValueError("Lighter pubkey must not be all zero")


def encode_change_pubkey(
    web3: Web3,
    account_index: int,
    api_key_index: int,
    pubkey: bytes,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> tuple[HexAddress, HexBytes]:
    """Encode a ``ZkLighter.changePubKey(accountIndex, apiKeyIndex, pubKey)`` call.

    Validates ``api_key_index`` and ``pubkey`` before encoding.

    :param web3:
        Web3 connection (for the ABI / encoding).

    :param account_index:
        The Lighter account index whose key is being set.

    :param api_key_index:
        The API-key slot (2-254 for user keys).

    :param pubkey:
        The API-key public key (see :py:func:`validate_lighter_pubkey`).

    :param zk_lighter:
        The ``ZkLighter`` L1 contract address.

    :return:
        ``(zk_lighter_address, calldata)``.
    """
    if not (0 <= api_key_index <= MAX_API_KEY_INDEX):
        raise ValueError(f"api_key_index must be 0..{MAX_API_KEY_INDEX}, got {api_key_index}")
    validate_lighter_pubkey(pubkey)

    zk_lighter = Web3.to_checksum_address(zk_lighter)
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    data = zk.functions.changePubKey(account_index, api_key_index, pubkey)._encode_transaction_data()
    return zk_lighter, HexBytes(data)


def build_change_pubkey_safe_tx(
    web3: Web3,
    safe: Safe,
    account_index: int,
    api_key_index: int,
    pubkey: bytes,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> SafeTx:
    """Build an (unsigned) Safe transaction calling ``ZkLighter.changePubKey``.

    The Safe is the Lighter account's L1 owner. Sign + execute it with the Safe
    owners, or post it to the Safe Transaction Service via
    :py:func:`propose_change_pubkey`.

    :return:
        An unsigned :class:`~safe_eth.safe.safe_tx.SafeTx`.
    """
    zk_lighter, data = encode_change_pubkey(web3, account_index, api_key_index, pubkey, zk_lighter)
    return safe.build_multisig_tx(zk_lighter, 0, bytes(data))


def propose_change_pubkey(
    web3: Web3,
    safe: Safe,
    proposer_private_key: str,
    account_index: int,
    api_key_index: int,
    pubkey: bytes,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> SafeTx:
    """Propose the ``changePubKey`` Safe transaction to the Safe Transaction Service.

    Builds + signs the transaction with one owner and posts it so the remaining
    Safe owners can co-sign in the Safe UI. Use this for a real multisig.

    Thin wrapper over :py:func:`eth_defi.safe.tx.propose_safe_transaction`.

    :param proposer_private_key:
        The proposing owner's private key (``0x``-prefixed).

    :return:
        The proposed :class:`~safe_eth.safe.safe_tx.SafeTx`.
    """
    # Imported here to keep the Safe Transaction Service dependency optional for
    # the build/encode/execute paths (which do not need it).
    from eth_defi.safe.tx import propose_safe_transaction

    zk_lighter, data = encode_change_pubkey(web3, account_index, api_key_index, pubkey, zk_lighter)
    logger.info("Proposing Lighter changePubKey via Safe %s -> %s (account %d, key %d)", safe.address, zk_lighter, account_index, api_key_index)
    return propose_safe_transaction(safe, zk_lighter, proposer_private_key, bytes(data))


def execute_change_pubkey(
    web3: Web3,
    safe: Safe,
    owner_private_key: str,
    account_index: int,
    api_key_index: int,
    pubkey: bytes,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> HexBytes:
    """Build, sign and execute the ``changePubKey`` Safe transaction immediately.

    For a single-owner Safe (or local simulation). For a real multisig prefer
    :py:func:`propose_change_pubkey` so the other owners can co-sign.

    :param owner_private_key:
        The executing owner's private key (``0x``-prefixed).

    :return:
        The executed transaction hash.
    """
    safe_tx = build_change_pubkey_safe_tx(web3, safe, account_index, api_key_index, pubkey, zk_lighter)
    safe_tx.sign(owner_private_key)
    tx_hash, _ = safe_tx.execute(tx_sender_private_key=owner_private_key)
    assert_transaction_success_with_explanation(web3, tx_hash)
    logger.info("Lighter changePubKey executed: %s", tx_hash.hex())
    return tx_hash

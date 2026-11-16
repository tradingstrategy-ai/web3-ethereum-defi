"""Solana account identities for the native vault data pipeline.

:py:data:`SOLANA_CHAIN_ID` reserves an in-house dataset partition for Solana
mainnet. It is not an EVM ``eth_chainId`` and must not select an EVM RPC reader.

Solana uses case-sensitive base58 encodings of `32-byte account addresses
<https://solana.com/docs/core/accounts>`_. Program-derived accounts are valid
identities even though they are `off the Ed25519 curve
<https://solana.com/docs/core/pda>`_. No curve-membership check belongs here.

The reversible ``solana-vault-`` key preserves the address bytes in storage
paths that normalise identifiers to lowercase. It is independent of the vault
provider; convert back to base58 for display, explorers and native API calls.
"""

import re

import base58

#: Reserved in-house Solana mainnet dataset ID, not a public EVM chain ID.
SOLANA_CHAIN_ID = 9992
#: All Solana account addresses, including PDAs, contain exactly 32 bytes.
PUBLIC_KEY_BYTES = 32
#: Maximum base58 text length for a 32-byte account.
MAX_BASE58_ADDRESS_LENGTH = 44

_VAULT_KEY = re.compile(r"solana-vault-[0-9a-f]{64}", re.IGNORECASE | re.ASCII)


def _decode_account(address: str) -> bytes:
    """Decode a canonical base58 account without stripping or folding its text.

    Check the encoded length before decoding to bound malformed input, and
    round-trip the text because base58 decoders can otherwise accept trailing
    whitespace. Every 32-byte value is an admissible account identity.

    :param address: Case-sensitive base58 account, without surrounding whitespace.
    :return: The original account's 32 bytes.
    :raises ValueError: If the encoding or decoded address width is invalid.
    """
    if not isinstance(address, str) or not PUBLIC_KEY_BYTES <= len(address) <= MAX_BASE58_ADDRESS_LENGTH:
        message = "Solana address must be a 32-byte base58 account"
        raise ValueError(message)
    account = base58.b58decode(address)
    if len(account) != PUBLIC_KEY_BYTES or base58.b58encode(account).decode("ascii") != address:
        message = "Solana address must be a canonical 32-byte base58 account"
        raise ValueError(message)
    return account


def is_solana_address(address: str) -> bool:
    """Check a case-sensitive base58 Solana account, including off-curve PDAs.

    This validates the address encoding, not whether the account currently
    exists or belongs to a particular cluster. It performs no network reads.

    :param address: Candidate base58 account without surrounding whitespace.
    :return: Whether the text canonically encodes exactly 32 account bytes.
    """
    try:
        _decode_account(address)
    except ValueError:
        return False
    return True


def is_solana_vault_address(address: str) -> bool:
    """Check a complete provider-neutral Solana vault storage key.

    A prefix alone is insufficient: storage keys contain exactly 64 hex
    characters. Uppercase input is accepted for later canonicalisation.

    :param address: Candidate ``solana-vault-`` storage identity.
    :return: Whether it represents exactly 32 bytes without extra text.
    """
    return isinstance(address, str) and _VAULT_KEY.fullmatch(address) is not None


def make_solana_vault_address(address: str) -> str:
    """Encode a native account as a lowercase-safe, reversible vault identity.

    Decode the original case-sensitive base58 text before converting to hex.
    This retains the key format already used by collected Neutral Trade data.

    :param address: Exact base58 Solana account, including program-derived accounts.
    :return: ``solana-vault-`` followed by the account's 64 lowercase hex digits.
    :raises ValueError: If the account encoding is invalid.
    """
    return "solana-vault-" + _decode_account(address).hex()


def decode_solana_vault_address(address: str) -> str:
    """Recover the exact native account from a provider-neutral vault identity.

    Re-encoding the stored bytes restores the original base58 case without
    requiring protocol-specific metadata or a network request.

    :param address: Complete ``solana-vault-`` identity containing 32 hex bytes.
    :return: Canonical case-sensitive base58 account for API and explorer use.
    :raises ValueError: If the storage key is malformed.
    """
    if not is_solana_vault_address(address):
        message = "Invalid Solana vault storage identity"
        raise ValueError(message)
    account = bytes.fromhex(address[len("solana-vault-") :])
    return base58.b58encode(account).decode("ascii")

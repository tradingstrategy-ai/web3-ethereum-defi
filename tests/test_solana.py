"""Native Solana identities across shared chain, address and vault storage APIs."""

from pathlib import Path

import base58
import pytest

from eth_defi.chain import CHAIN_HOMEPAGES, CHAIN_NAMES, get_chain_homepage, get_chain_id_by_name
from eth_defi.neutral_trade.constants import SOLANA_DATASET_CHAIN_ID
from eth_defi.neutral_trade.constants import make_solana_vault_address as legacy_make_solana_vault_address
from eth_defi.solana import SOLANA_CHAIN_ID, decode_solana_vault_address, is_solana_address, is_solana_vault_address, make_solana_vault_address
from eth_defi.utils import is_good_multichain_address
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

#: Reviewed native account; its mixed base58 case is part of the identity.
ACCOUNT = "J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ"


def test_reserved_solana_dataset() -> None:
    """Reserve one shared mainnet dataset independently of the REST provider.

    Existing collected data and legacy imports must use the same reservation;
    display metadata and reverse name lookup must resolve it consistently.

    :return: ``None`` after checking the shared and compatibility registrations.
    """
    assert SOLANA_CHAIN_ID == 9992  # noqa: PLR2004 - existing persisted dataset reservation.
    assert SOLANA_DATASET_CHAIN_ID == SOLANA_CHAIN_ID
    assert CHAIN_NAMES[SOLANA_CHAIN_ID] == "Solana"
    assert CHAIN_HOMEPAGES[SOLANA_CHAIN_ID]["homepage"] == "https://solana.com"
    assert get_chain_id_by_name("solana") == SOLANA_CHAIN_ID
    assert get_chain_homepage(SOLANA_CHAIN_ID) == ("Solana", "https://solana.com")
    assert legacy_make_solana_vault_address(ACCOUNT) == make_solana_vault_address(ACCOUNT)


@pytest.mark.parametrize("separator", ["-", ","])
def test_raw_solana_identity_survives_shared_storage(tmp_path: Path, separator: str) -> None:
    """Make native input and historical storage keys resolve to the same vault.

    String parsing, hash lookups and pickle persistence must use one canonical
    identity, with the exact original address recoverable for display and APIs.

    :param tmp_path: Isolated vault metadata database.
    :param separator: Supported separator between dataset and address.
    :return: ``None`` after checking parsing, lookup and persistence round trips.
    """
    native = VaultSpec(SOLANA_CHAIN_ID, ACCOUNT)
    encoded = VaultSpec(SOLANA_CHAIN_ID, make_solana_vault_address(ACCOUNT))
    assert native == encoded
    assert hash(native) == hash(encoded)
    assert VaultSpec.parse_string(f"{SOLANA_CHAIN_ID}{separator}{ACCOUNT}", separator=separator) == encoded
    assert VaultSpec.parse_string(encoded.as_string_id()) == native
    assert decode_solana_vault_address(native.vault_address) == ACCOUNT
    path = tmp_path / "vaults.pickle"
    VaultDatabase(rows={native: {"Address": native.vault_address}}).write(path)
    stored = VaultDatabase.read(path)
    assert stored.rows[encoded]["Address"] == native.vault_address
    assert decode_solana_vault_address(next(iter(stored.rows)).vault_address) == ACCOUNT


def test_base58_case_is_not_folded() -> None:
    """Keep two accounts differing only in letter case as distinct identities.

    Change an interior base58 digit rather than the leading magnitude digit so
    both encodings remain canonical 32-byte values.

    :return: ``None`` after verifying distinct keys and exact reverse conversion.
    """
    variant = ACCOUNT[:-1] + ACCOUNT[-1].lower()
    assert is_solana_address(ACCOUNT) and is_solana_address(variant)
    first, second = VaultSpec(SOLANA_CHAIN_ID, ACCOUNT), VaultSpec(SOLANA_CHAIN_ID, variant)
    assert first != second
    assert decode_solana_vault_address(first.vault_address) == ACCOUNT
    assert decode_solana_vault_address(second.vault_address) == variant


@pytest.mark.parametrize("account_bytes", [bytes(32), bytes([255]) * 32, bytes(range(32))])
def test_all_account_bytes_are_accepted(account_bytes: bytes) -> None:
    """Accept leading-zero addresses and off-curve values without a signer check.

    Solana vaults can be PDAs, which have no private signing key. Requiring an
    Ed25519 point would reject legitimate account identities.

    :param account_bytes: Exact 32-byte account value, including off-curve bytes.
    :return: ``None`` after validating and round-tripping its canonical encoding.
    """
    account = base58.b58encode(account_bytes).decode("ascii")
    assert is_solana_address(account)
    assert decode_solana_vault_address(make_solana_vault_address(account)) == account


@pytest.mark.parametrize("address", ["", "1" * 31, "1" * 33, "z" * 44, "0" * 32, "é" * 32, " " + ACCOUNT, ACCOUNT + " ", ACCOUNT + "\n", "A" * 10000])
def test_invalid_accounts_are_rejected(address: str) -> None:
    """Reject noncanonical encodings before admitting them as native identities.

    Neither valid-looking text length nor a base58 decoder's whitespace
    tolerance establishes a correct 32-byte Solana account.

    :param address: Wrong alphabet, width or whitespace-bearing account text.
    :return: ``None`` after checking validators and the conversion boundary.
    """
    assert not is_solana_address(address)
    assert not is_good_multichain_address(address)
    with pytest.raises(ValueError):
        make_solana_vault_address(address)


@pytest.mark.parametrize("address", ["solana-vault-", "solana-vault-xyz", "solana-vault-" + "00" * 31, "solana-vault-" + "gg" * 32, "solana-vault-" + "00" * 33])
def test_malformed_storage_keys_are_rejected(address: str) -> None:
    """Require the complete synthetic identity instead of trusting its prefix.

    Malformed keys cannot enter shared metadata under an otherwise valid
    native dataset ID or be converted back to an explorer address.

    :param address: Truncated, oversized or non-hexadecimal storage key.
    :return: ``None`` after rejecting the candidate at both boundaries.
    """
    assert not is_solana_vault_address(address)
    assert not is_good_multichain_address(address)
    with pytest.raises(ValueError):
        decode_solana_vault_address(address)
    with pytest.raises(AssertionError, match="Unsupported vault address"):
        VaultSpec(SOLANA_CHAIN_ID, address)


def test_chain_scoping_and_evm_compatibility() -> None:
    """Keep Solana identities in their reserved partition and retain EVM behaviour.

    Uppercase hex storage input is safe to canonicalise. Raw base58 must never
    be paired with an EVM chain or replaced by an EVM/platform ID on Solana.

    :return: ``None`` after checking incompatible pairs and existing EVM casing.
    """
    encoded = make_solana_vault_address(ACCOUNT)
    assert VaultSpec(SOLANA_CHAIN_ID, encoded.upper()).vault_address == encoded
    assert is_good_multichain_address(ACCOUNT)
    assert is_good_multichain_address(ACCOUNT, SOLANA_CHAIN_ID)
    for address in (ACCOUNT, encoded):
        with pytest.raises(AssertionError, match="Unsupported vault address"):
            VaultSpec(1, address)
    for address in ("0x" + "ab" * 20, "apex-vault-123"):
        with pytest.raises(AssertionError, match="Unsupported vault address"):
            VaultSpec(SOLANA_CHAIN_ID, address)
    assert VaultSpec(1, "0x" + "Ab" * 20).vault_address == "0x" + "ab" * 20

"""Check the reviewed AlphaPing inventory and adapter description precedence."""

from collections import Counter

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.handwritten_metadata import (
    ALPHAPING_IPOR_VAULT_METADATA,
    ALPHAPING_MORPHO_VAULT_METADATA,
    ALPHAPING_VAULT_METADATA,
    MORINI_CAPITAL_VAULT_METADATA,
    get_handwritten_vault_metadata,
)

FOREX_ADDRESS = "0x153Bd1abE60104Bd46aa05a27fA12D1346D64A57"


def test_alphaping_reviewed_inventory() -> None:
    """Keep every reviewed deployment, including legacy and partner products.

    The address list is the independent 2026-10-28 review baseline, preventing
    lost entries or accidental inclusion of excluded installer/test contracts.
    """
    ethereum_morpho = {
        "0x0bf0164d17469241b6e086da4016dcc54feaa334",
        "0x153bd1abe60104bd46aa05a27fa12d1346d64a57",
        "0x35cbe8542e70fa2f7f9cdf129f19e593f4b4f560",
        "0x610f5b68bd1eed68af649a3fd3dc2caa1ee4ae7e",
        "0x951a9f4a2ce19b9dea6b37e691d076a345b6c0f8",
        "0x9f39b13bb472126d6937bf25a39338e664eefa82",
        "0xbb5ad5d522684de9dd3f545fa6d6ac5bbca4710e",
        "0xd0634bf57921ef4068f284f5e78a3f2623e20129",
        "0xd8cd72a2eb7a4312404c00aed5172c0026da0c27",
        "0xeb6719c14ba01bf1d6bfb78a65f63f12fabf202c",
        "0xf1ca44eea3a4effcb195a970a2f1d8553f76f9a1",
        "0xfa7ed49eb24a6117d8a3168eee69d26b45c40c63",
        "0xf9b85b19f6885511165505299648e6d052e8d4c8",
        "0xda4d1a3187eba9ecaa137867bd4541f78d51638e",
        "0xce22b5fb17ccbc0c5d87dc2e0df47dd71e3adc0a",
        "0xb0f05e4de970a1aaf77f8c2f823953a367504ba9",
        "0x89610f2125a54547a7fb2a1b7926be734a632255",
        "0x874e7fb1abb4c27a7bbeeefa39e888b63181939c",
        "0x6701957aaa39e9352a42729cc65436a4c945cb90",
        "0x6619f92861c760ad11ba0d56e8ed63a33ecce22b",
        "0x52317a47585a6acdfbd7a29b494c3e2baae96abc",
        "0x47fe8ab9ee47dd65c24df52324181790b9f47efc",
        "0x3c124fb80e6eedd88d108c2f04013ae5cd571abc",
        "0xa79a5fb6f619da3ed71087fb5cd33b16a5b666b5",
        "0x76dedaf4ec78777716bdeba484037c9d5fbf5d11",
    }
    expected = {(1, address) for address in ethereum_morpho} | {
        (8453, "0x44dd77d51629987d9555c762bf09903928c70206"),
        (8453, "0xea47a35aaa60302b4816d920856ddc708ad181f6"),
        (8453, "0x4a4de4757d2a04d1e12bcc21068288caad6e19a7"),
        (8453, "0x2365975ca06f99d5da5acfc70f2581b8d194f8aa"),
        (42161, "0x4c289073eb6679393890b85eec9d604271c18c28"),
        (42161, "0x23faff4f253b85834d9d37f97ea807ec46d1ec9e"),
        (42161, "0x0f841bd670eaeb6be0a173c06efefbad09abb0f4"),
        (999, "0x242572d6f1af7111bca807ecdd0f74108ceaed5d"),
    }
    assert set(ALPHAPING_MORPHO_VAULT_METADATA) == expected
    assert set(ALPHAPING_IPOR_VAULT_METADATA) == {
        (1, "0xb4790cfdf1df71c2e4ac2f5c43dbcb975ad447ae"),
        (1, "0x29d322dd088e9b9d1416f43188954f08748fafbb"),
        (1, "0xb530a1b5259a71187f1d69acf0488f102637a3ed"),
    }
    assert Counter(chain for chain, _ in ALPHAPING_VAULT_METADATA) == {1: 28, 8453: 4, 42161: 3, 999: 1}
    for (_, address), metadata in ALPHAPING_VAULT_METADATA.items():
        assert address == address.lower()
        assert Web3.is_address(address)
        assert metadata.short_description.endswith(".")
        assert "\n" not in metadata.short_description
        assert metadata.link in metadata.description
        assert "risk" in metadata.description.lower()


def test_handwritten_metadata_normalises_addresses_and_isolates_chains() -> None:
    """Resolve renamed products by address without losing existing Morini copy."""
    metadata = get_handwritten_vault_metadata(1, FOREX_ADDRESS)
    assert metadata is get_handwritten_vault_metadata(1, FOREX_ADDRESS.lower())
    assert metadata.short_description == "USDC lending against tokenised Turkish currency and equity strategies."
    assert get_handwritten_vault_metadata(8453, FOREX_ADDRESS) is None
    assert get_handwritten_vault_metadata(1, "0x0000000000000000000000000000000000000001") is None
    for (chain_id, address), morini_metadata in MORINI_CAPITAL_VAULT_METADATA.items():
        assert get_handwritten_vault_metadata(chain_id, address) is morini_metadata


@pytest.mark.parametrize("vault_class", [MorphoV1Vault, MorphoV2Vault])
@pytest.mark.parametrize("key", tuple(ALPHAPING_MORPHO_VAULT_METADATA))
def test_morpho_handwritten_descriptions_without_rpc(vault_class: type[MorphoV1Vault | MorphoV2Vault], key: tuple[int, str]) -> None:
    """Read every reviewed description without requiring API or RPC metadata."""
    vault = vault_class(Web3(), VaultSpec(*key))
    metadata = ALPHAPING_MORPHO_VAULT_METADATA[key]
    assert vault.description == metadata.description
    assert vault.short_description == metadata.short_description


@pytest.mark.parametrize("vault_class", [MorphoV1Vault, MorphoV2Vault])
def test_unrelated_morpho_vault_keeps_default_descriptions(vault_class: type[MorphoV1Vault | MorphoV2Vault]) -> None:
    """Preserve the normal empty description for unreviewed Morpho products."""
    vault = vault_class(Web3(), VaultSpec(1, "0x0000000000000000000000000000000000000001"))
    assert vault.description is None
    assert vault.short_description is None


@pytest.mark.parametrize("key", tuple(ALPHAPING_IPOR_VAULT_METADATA))
@pytest.mark.parametrize("api_metadata", [None, {"description": "An obsolete description."}])
def test_ipor_handwritten_descriptions_override_missing_or_stale_metadata(key: tuple[int, str], api_metadata: dict | None) -> None:
    """Legacy IPOR copy remains available with missing or outdated API data."""
    vault = IPORVault(Web3(), VaultSpec(*key))
    vault.__dict__["ipor_metadata"] = api_metadata
    metadata = ALPHAPING_IPOR_VAULT_METADATA[key]
    assert vault.description == metadata.description
    assert vault.short_description == metadata.short_description


@pytest.mark.parametrize("api_metadata", [None, {"description": "First sentence. More detail.", "prospectus_link": "https://example.com/prospectus"}])
def test_unrelated_ipor_vault_keeps_api_copy(api_metadata: dict | None) -> None:
    """Retain IPOR's existing prospectus and first-sentence behaviour."""
    vault = IPORVault(Web3(), VaultSpec(1, "0x0000000000000000000000000000000000000001"))
    vault.__dict__["ipor_metadata"] = api_metadata
    if api_metadata:
        assert vault.description == "First sentence. More detail.\n\n[View prospectus](https://example.com/prospectus)"
        assert vault.short_description == "First sentence."
    else:
        assert vault.description is None
        assert vault.short_description is None


def test_only_explicitly_deprecated_ipor_deployment_is_described_as_deprecated() -> None:
    """A hidden listing alone must not be treated as evidence of deprecation."""
    deprecated = ALPHAPING_IPOR_VAULT_METADATA[1, "0x29d322dd088e9b9d1416f43188954f08748fafbb"]
    assert "deprecated" in deprecated.description
    assert "Deprecated" in deprecated.short_description
    assert all("deprecated" not in metadata.description for metadata in ALPHAPING_IPOR_VAULT_METADATA.values() if metadata is not deprecated)

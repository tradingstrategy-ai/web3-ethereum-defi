"""Test complete offchain Enzyme vault descriptions."""

import pytest

from eth_defi.enzyme.offchain_metadata import EnzymeVaultMetadata, create_enzyme_fallback_metadata, create_enzyme_vault_link, resolve_enzyme_vault_metadata


@pytest.mark.parametrize("architecture", ["blue", "onyx"])
def test_every_enzyme_architecture_has_complete_fallback_copy(architecture: str) -> None:
    """Provide non-empty listing descriptions without a manager override."""

    metadata = create_enzyme_fallback_metadata(architecture, "Example vault")

    assert metadata.short_description
    assert metadata.description
    assert "Example vault" in metadata.description


def test_curated_enzyme_copy_overrides_only_the_provided_fields() -> None:
    """Retain the fallback field when a curator supplies partial metadata."""

    metadata = resolve_enzyme_vault_metadata(
        "blue",
        "Example vault",
        EnzymeVaultMetadata(description="Curator-supplied strategy narrative."),
    )

    assert metadata.description == "Curator-supplied strategy narrative."
    assert metadata.short_description == "Enzyme Blue tokenised digital-asset investment vehicle."


@pytest.mark.parametrize(
    ("chain_id", "network"),
    [(1, "ethereum"), (137, "polygon"), (8453, "base"), (42161, "arbitrum")],
)
def test_enzyme_vault_link_uses_address_and_network(chain_id: int, network: str) -> None:
    """Create one canonical direct URL format for Blue and Onyx adapters."""

    address = "0x000000000000000000000000000000000000bEEF"

    assert create_enzyme_vault_link(chain_id, address) == f"https://app.enzyme.finance/vault/0x000000000000000000000000000000000000bEEF?network={network}"


def test_enzyme_vault_link_rejects_unsupported_network() -> None:
    """Fail explicitly instead of publishing a malformed unknown-chain URL."""

    with pytest.raises(ValueError, match="Unsupported Enzyme chain"):
        create_enzyme_vault_link(999_999, "0x000000000000000000000000000000000000bEEF")

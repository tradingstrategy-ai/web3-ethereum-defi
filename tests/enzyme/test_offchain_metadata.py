"""Test complete offchain Enzyme vault descriptions."""

import pytest

from eth_defi.enzyme.offchain_metadata import EnzymeVaultMetadata, create_enzyme_fallback_metadata, resolve_enzyme_vault_metadata


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

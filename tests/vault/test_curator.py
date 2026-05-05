"""Test vault curator detection."""

from eth_defi.vault.curator import identify_curator


def test_identify_anthias_labs_felix_vault() -> None:
    """Felix Morpho vault names resolve to Anthias Labs.

    Felix's terms designate Anthias Labs as Curator, Allocator, and
    Guardian for the Felix Morpho vaults, while exported vault names use
    the Felix brand.
    """

    slug = identify_curator(
        chain_id=999,
        vault_token_symbol="feUSDC",
        vault_name="Felix USDC",
        vault_address="0x8a862fd6c12f9ad34c9c2ff45ab2b6712e8cea27",
        protocol_slug="morpho",
    )

    assert slug == "anthias-labs"

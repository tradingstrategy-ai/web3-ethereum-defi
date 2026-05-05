"""Test vault curator detection."""

from eth_defi.vault.curator import get_curator_name, identify_curator, is_protocol_curator


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


def test_identify_smokehouse_as_steakhouse_financial() -> None:
    """Smokehouse vault names resolve to Steakhouse Financial."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="smokeUSDC",
        vault_name="Trust Wallet Morpho Smokehouse USDC",
        vault_address="0x0000000000000000000000000000000000000001",
        protocol_slug="morpho",
    )

    assert slug == "steakhouse-financial"


def test_identify_telosc_short_name() -> None:
    """TelosC vault names resolve to Telos Consilium."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="eulerUSDC",
        vault_name="TelosC Stream 3",
        vault_address="0x0000000000000000000000000000000000000002",
        protocol_slug="euler",
    )

    assert slug == "telosc"


def test_identify_kappa_lab_fire_liquidity_provider() -> None:
    """Fire Liquidity Provider vault names resolve to Kappa Lab."""

    slug = identify_curator(
        chain_id=9999,
        vault_token_symbol="FLP",
        vault_name="Fire Liquidity Provider",
        vault_address="hibachi:vault:3",
        protocol_slug="hibachi",
    )

    assert slug == "kappa-lab"


def test_identify_api3_vault() -> None:
    """Api3 Morpho vault names resolve to API3."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="Api3CoreUSDC",
        vault_name="Api3 Core USDC",
        vault_address="0xb3f4d94a209045ef35661e657db9adac584141f1",
        protocol_slug="morpho",
    )

    assert slug == "api3"
    assert get_curator_name("api3") == "API3"


def test_identify_hakutora_vault() -> None:
    """Hakutora Morpho vault names resolve to Hakutora."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="hUSDC",
        vault_name="Hakutora USDC",
        vault_address="0x974c8fbf4fd795f66b85b73ebc988a51f1a040a9",
        protocol_slug="morpho",
    )

    assert slug == "hakutora"
    assert get_curator_name("hakutora") == "Hakutora"


def test_identify_pangolins_vault() -> None:
    """Pangolins Morpho vault names resolve to Pangolins."""

    slug = identify_curator(
        chain_id=8453,
        vault_token_symbol="pUSDC",
        vault_name="Pangolins USDC",
        vault_address="0x1401d1271c47648ac70cbcdfa3776d4a87ce006b",
        protocol_slug="morpho",
    )

    assert slug == "pangolins"
    assert get_curator_name("pangolins") == "Pangolins"


def test_identify_gains_network_protocol_curator() -> None:
    """Gains Network protocol vaults resolve to the exported protocol slug."""

    slug = identify_curator(
        chain_id=42161,
        vault_token_symbol="gDAI",
        vault_name="gDAI",
        vault_address="0x0000000000000000000000000000000000000003",
        protocol_slug="gains-network",
    )

    assert slug == "gains-network"
    assert is_protocol_curator("gains-network")
    assert get_curator_name("gains-network") == "Gains Network"


def test_identify_legacy_gtrade_as_gains_network() -> None:
    """Legacy gTrade protocol slug resolves to Gains Network."""

    slug = identify_curator(
        chain_id=42161,
        vault_token_symbol="gDAI",
        vault_name="gDAI",
        vault_address="0x0000000000000000000000000000000000000004",
        protocol_slug="gtrade",
    )

    assert slug == "gains-network"

"""Test vault curator detection."""

from eth_defi.vault.curator import get_curator_name, identify_curator, is_protocol_curator


def test_identify_felix_vault() -> None:
    """Felix Morpho vault names resolve to the Felix curator.

    Felix is a Morpho-verified curator on HyperEVM.  Although Anthias Labs
    acts as risk curator/allocator/guardian for Felix vaults, vault names
    use the Felix brand and are now mapped to the felix feeder slug.
    """

    slug = identify_curator(
        chain_id=999,
        vault_token_symbol="feUSDC",
        vault_name="Felix USDC",
        vault_address="0x8a862fd6c12f9ad34c9c2ff45ab2b6712e8cea27",
        protocol_slug="morpho",
    )

    assert slug == "felix"


def test_identify_alphagrowth_vault() -> None:
    """AlphaGrowth Euler vault names resolve to the AlphaGrowth curator."""

    slug = identify_curator(
        chain_id=8453,
        vault_token_symbol="agUSDC",
        vault_name="AlphaGrowth USDC Base Vault",
        vault_address="0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e",
        protocol_slug="euler",
    )

    assert slug == "alphagrowth"
    assert get_curator_name("alphagrowth") == "AlphaGrowth"


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


def test_identify_grvt_glp_protocol_curator() -> None:
    """GRVT in-house market maker GLP resolves to the GRVT protocol curator.

    The GLP (Grvt Liquidity Provider) is GRVT's own market making vault,
    identified by its synthetic system vault address and classified as a
    protocol-managed curator rather than a third-party manager.

    1. Identify the GLP vault by its system vault address.
    2. Confirm it resolves to the ``grvt`` protocol curator.
    3. Confirm protocol-curator classification and display name.
    """

    # 1. GLP synthetic address is the lowercased GRVT vault_id
    slug = identify_curator(
        chain_id=325,
        vault_token_symbol="GLP",
        vault_name="Grvt Liquidity Provider (GLP)",
        vault_address="vlt:34dtzyg6lhkgm49je5aabi9tebw",
        protocol_slug="grvt",
        manager_name="Grvt",
    )

    # 2. Resolves to the GRVT protocol curator slug
    assert slug == "grvt"

    # 3. Classified as protocol-managed with a display name
    assert is_protocol_curator("grvt")
    assert get_curator_name("grvt") == "GRVT"


def test_identify_lighter_xlp_protocol_curator() -> None:
    """Lighter experimental system pool XLP resolves to the Lighter protocol curator.

    The XLP (Experimental Liquidity Provider) is a protocol-run Lighter
    pool for experimental markets and is treated like the LLP system pool.

    1. Identify the XLP pool by its synthetic system pool address.
    2. Confirm it resolves to the ``lighter`` protocol curator.
    3. Confirm protocol-curator classification.
    """

    # 1. XLP synthetic pool address (account_index 281474976680784)
    slug = identify_curator(
        chain_id=9998,
        vault_token_symbol="XLP",
        vault_name="Experimental Liquidity Provider (XLP)",
        vault_address="lighter-pool-281474976680784",
        protocol_slug="lighter",
    )

    # 2. Resolves to the Lighter protocol curator slug
    assert slug == "lighter"

    # 3. Classified as protocol-managed
    assert is_protocol_curator("lighter")


def test_identify_grvt_curator_by_manager_name() -> None:
    """GRVT third-party curators are detected from the manager name.

    GRVT brands the curator in a separate manager field while the vault
    name holds only the strategy name, so curator detection must match
    against the manager name.

    1. Identify a GRVT vault whose brand is only in the manager name.
    2. Confirm it resolves to the third-party curator, not a protocol one.
    """

    # 1. Vault name is the strategy; manager carries the curator brand
    slug = identify_curator(
        chain_id=325,
        vault_token_symbol="Ethereum M",
        vault_name="Ethereum Moving Average Long/Short",
        vault_address="vlt:abc123",
        protocol_slug="grvt",
        manager_name="Gerhard - Bitcoin Strategy",
    )

    # 2. Resolves to the third-party Gerhard curator
    assert slug == "gerhard-bitcoin-strategy"
    assert not is_protocol_curator("gerhard-bitcoin-strategy")


def test_identify_lighter_pmalt_pool_curator() -> None:
    """Lighter third-party pool operators are detected from the pool name.

    Lighter pool names embed the operator brand, so name matching alone
    resolves third-party operators such as pmalt.

    1. Identify a Lighter pool by its operator-branded name.
    2. Confirm it resolves to the third-party curator.
    """

    # 1. Lighter pool name carries the operator brand
    slug = identify_curator(
        chain_id=9998,
        vault_token_symbol="pmalt",
        vault_name="pmalt",
        vault_address="lighter-pool-281474976552918",
        protocol_slug="lighter",
    )

    # 2. Resolves to the pmalt curator
    assert slug == "pmalt"
    assert get_curator_name("pmalt") == "pmalt"


def test_identify_vault_name_sweep_curators() -> None:
    """Curators discovered from the vault-name sweep resolve on their vaults.

    These were added by mining unmatched vault names across Morpho, Euler,
    Lagoon and Kiln Metavault.  Most match via their YAML ``name``; Keyring
    matches via an explicit short-brand pattern because its vaults are named
    "Keyring zkVerified Cluster".

    1. Resolve a representative vault for each new curator.
    2. Confirm the anonymous Lagoon "Der" curator resolves on a Der vault.
    3. Confirm the calendar month "August" does not false-match August Digital.
    """

    # 1. New curators detected by their brand in the vault name
    cases = {
        ("morpho", "Coinshift USDC"): "coinshift",
        ("morpho", "Hyperbeat USDC Lending Optimizer"): "hyperbeat",
        ("euler", "Lista DAO USD1 Vault"): "lista-dao",
        ("euler", "Keyring zkVerified Cluster"): "keyring-network",
        ("kiln-metavault", "Trust Wallet AAVE v3 USDT"): "trust-wallet",
        ("kiln-metavault", "Cool Wallet AAVEv3 USDC"): "cool-wallet",
        ("lagoon-finance", "Mt Pelerin – USD strategy pool"): "mt-pelerin",
        ("euler", "HypurrFi Earn USDC"): "hypurrfi",
        ("lagoon-finance", "DAMM Stablecoin Fund"): "damm-capital",
        ("morpho", "August USDC"): "august-digital",
    }
    for (protocol_slug, name), expected in cases.items():
        slug = identify_curator(
            chain_id=1,
            vault_token_symbol="",
            vault_name=name,
            vault_address="0x0000000000000000000000000000000000000000",
            protocol_slug=protocol_slug,
        )
        assert slug == expected, f"{name!r} -> {slug!r}, expected {expected!r}"

    # 2. Anonymous Lagoon "Der" curator
    assert identify_curator(1, "", "Der USDC", "0x0", "lagoon-finance") == "der"

    # 3. The calendar month must not be mistaken for August Digital
    assert identify_curator(1, "", "Prize imToken August Campaign", "0x0", "morpho") is None

"""Test vault curator detection."""

from pathlib import Path

from eth_defi.midas.registry import iter_midas_registry_products
from eth_defi.vault.curator import build_curator_metadata_json, get_curator_name, identify_curator, is_protocol_curator


def test_identify_securitize_fund_curators() -> None:
    """Map high-value DSToken funds to their asset-manager curators."""

    expected_curators = {
        "0x7712c34205737192402172409a8f7ccef8aa2aec": "blackrock",
        "0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041": "blackrock",
        "0x17418038ecf73ba4026c4f428547bf099706f27b": "apollo",
        "0x2255718832bc9fd3be1caf75084f4803da14ff01": "vaneck",
        "0x51c2d74017390cbbd30550179a16a1c28f7210fc": "bny-investments",
        "0x252739487c1fa66eaeae7ced41d6358ab2a6bca9": "arca",
        "0x0324dd195d0cd53f9f07bee6a48ee7a20bad738f": "spice-vc",
        "0xda2ffa104356688e74d9340519b8c17f00d7752e": "hamilton-lane",
        "0x1f41e42d0a9e3c0dd3ba15b527342783b43200a9": "blockchain-capital",
        "0xc0c61c29ef8beabc694987c93e5fe4af647042e7": "cosimo-digital",
        "0x682ef9cc637ef56577092b29ae9275a629aae7db": "science-inc",
        "0x5e17f6f450dcb0bc69b232ea554e224d7e88067a": "protos-asset-management",
    }

    for address, expected_slug in expected_curators.items():
        assert identify_curator(1, "", "Securitize DSToken", address, "securitize") == expected_slug


def test_securitize_fund_curator_metadata_includes_logo() -> None:
    """Export generic logo URLs for Securitize curators with verified assets."""

    for slug in ("blackrock", "apollo", "vaneck", "bny-investments", "arca", "spice-vc", "hamilton-lane", "blockchain-capital", "cosimo-digital", "science-inc"):
        metadata = build_curator_metadata_json(
            Path(f"eth_defi/data/feeds/curators/{slug}.yaml"),
            public_url="https://example.com",
        )

        assert metadata["logos"]["generic"] == f"https://example.com/curator-metadata/{slug}/generic.png"


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


def test_identify_fasanara_midas_mfone_vault() -> None:
    """Resolve both Midas spellings of the Fasanara-managed mF-ONE product."""

    for vault_name in ("Midas mF-ONE", "Midas mFONE"):
        slug = identify_curator(
            chain_id=1,
            vault_token_symbol="mFONE",
            vault_name=vault_name,
            vault_address="0x238a700eD6165261Cf8b2e544ba797BC11e466Ba",
            protocol_slug="midas",
        )

        assert slug == "fasanara"


def test_identify_apollo_crypto_midas_mapollo_vault() -> None:
    """Resolve Midas' Apollo Crypto mAPOLLO product by its distinct token name."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="mAPOLLO",
        vault_name="Midas mAPOLLO",
        vault_address="0x7CF9DEC92ca9FD46f8d86e7798B72624Bc116C05",
        protocol_slug="midas",
    )

    assert slug == "apollo-crypto"


def test_identify_edge_capital_midas_medge_vaults() -> None:
    """Resolve every deployed Midas mEDGE product to the Edge Capital alias.

    Midas names Edge Capital as mEDGE's risk advisor.  The detection is based
    on the exact product name, rather than an address list, because mEDGE is
    deployed on multiple chains and may be deployed on additional ones later.
    """

    medge_products = [product for product in iter_midas_registry_products() if product.symbol == "mEDGE"]

    assert medge_products
    for product in medge_products:
        assert product.token is not None
        slug = identify_curator(
            chain_id=product.chain_id,
            vault_token_symbol=product.symbol,
            vault_name=f"Midas {product.symbol}",
            vault_address=product.token,
            protocol_slug="midas",
        )

        assert slug == "edge-capital", f"{product.network} mEDGE resolved to {slug!r}"

    # Keep the product match exact: ``mEDGE`` must not also claim a similarly
    # named future Midas product.
    assert (
        identify_curator(
            chain_id=1,
            vault_token_symbol="mEDGES",
            vault_name="Midas mEDGES",
            vault_address="0x0000000000000000000000000000000000000013",
            protocol_slug="midas",
        )
        != "edge-capital"
    )


def test_edge_capital_reuses_ultrayield_logo() -> None:
    """Use the UltraYield asset when the Edge Capital alias has no separate logo."""

    metadata = build_curator_metadata_json(
        Path("eth_defi/data/feeds/curators/edge-capital.yaml"),
        public_url="https://example.com",
    )

    assert metadata["logos"]["generic"] == "https://example.com/curator-metadata/ultrayield/generic.png"


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


def test_identify_euler_curator_by_entity_metadata() -> None:
    """Euler manager names resolve by exact ``euler-entity`` YAML metadata."""

    slug = identify_curator(
        chain_id=146,
        vault_token_symbol="",
        vault_name="Sonic USDC Market",
        vault_address="0x0000000000000000000000000000000000000006",
        protocol_slug="euler",
        manager_name="mev-capital",
    )

    assert slug == "mev-capital"


def test_identify_ipor_curator_by_atomist_metadata() -> None:
    """IPOR manager names resolve by exact ``ipor-atomist`` YAML metadata."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="Prime HELOC Loop",
        vault_address="0x0000000000000000000000000000000000000009",
        protocol_slug="ipor-fusion",
        manager_name="TAU Labs",
    )

    assert slug == "tau"


def test_protocol_manager_metadata_precedes_ordinary_vault_name_match() -> None:
    """Exact protocol manager metadata wins over non-priority vault-name matches."""

    slug = identify_curator(
        chain_id=146,
        vault_token_symbol="",
        vault_name="Gauntlet Sonic USDC Market",
        vault_address="0x0000000000000000000000000000000000000010",
        protocol_slug="euler",
        manager_name="mev-capital",
    )

    assert slug == "mev-capital"


def test_identify_curator_tolerates_none_manager_name() -> None:
    """Unknown manager names can be passed as ``None``."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="Unbranded USDC Market",
        vault_address="0x0000000000000000000000000000000000000011",
        protocol_slug="morpho",
        manager_name=None,
    )

    assert slug is None


def test_identify_morpho_curator_by_curator_metadata() -> None:
    """Morpho manager names resolve by exact ``morpho-curator`` YAML metadata."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="USDC Prime",
        vault_address="0x0000000000000000000000000000000000000007",
        protocol_slug="morpho",
        manager_name="Gauntlet",
    )

    assert slug == "gauntlet"


def test_identify_m11_credit_curator() -> None:
    """M11 Credit resolves from Morpho manager metadata and M11C vault names."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="Level lvlUSD",
        vault_address="0x2c3cc1c02856894345797cf6ee76ae76ac0f4031",
        protocol_slug="morpho",
        manager_name="M11C",
    )

    assert slug == "m11-credit"
    assert get_curator_name("m11-credit") == "M11 Credit"

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="M11C Level lvlUSD",
        vault_address="0x2c3cc1c02856894345797cf6ee76ae76ac0f4031",
        protocol_slug="morpho",
    )

    assert slug == "m11-credit"


def test_identify_lagoon_curator_by_curator_metadata() -> None:
    """Lagoon manager names resolve by exact ``lagoon-curator`` YAML metadata."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="Stablecoin Fund",
        vault_address="0x0000000000000000000000000000000000000008",
        protocol_slug="lagoon-finance",
        manager_name="DAMM Capital",
    )

    assert slug == "damm-capital"

    slug = identify_curator(
        chain_id=8453,
        vault_token_symbol="",
        vault_name="722Capital-USDC",
        vault_address="0xb09f761cb13baca8ec087ac476647361b6314f98",
        protocol_slug="lagoon-finance",
        manager_name="722 Capital",
    )

    assert slug == "722-capital"


def test_identify_t3tris_curator_by_curator_metadata() -> None:
    """T3tris manager names resolve by exact ``t3tris-curator`` YAML metadata."""

    slug = identify_curator(
        chain_id=42161,
        vault_token_symbol="",
        vault_name="First - USDC",
        vault_address="0x98e43a491a464f0886bc5e57207c340bbed0d01f",
        protocol_slug="t3tris",
        manager_name="First Capital",
    )

    assert slug == "first-capital"
    assert get_curator_name("first-capital") == "First Capital"


def test_identify_asseto_curator_by_priority_partner_role() -> None:
    """Asseto investment manager/advisor names resolve through curator YAML."""

    assert identify_curator(1, "", "", "0x0", "asseto", "CMS Asset Management (HK)") == "cms-asset-management-hk"
    assert identify_curator(1, "", "", "0x0", "asseto", "DL Holdings") == "dl-holdings"
    assert identify_curator(1, "", "", "0x0", "asseto", "Four Seasons") == "four-seasons"
    assert identify_curator(1, "", "", "0x0", "asseto", "DFZQ / Orient Securities International") == "dfzq"


def test_identify_rockawayx_dashboard_vaults_by_address() -> None:
    """RockawayX Dune dashboard vaults resolve by exact address.

    Some RockawayX curated vaults carry partner or asset names instead of the
    RockawayX brand, and two Midas Prime Morpho vaults would otherwise resolve
    to the Midas curator by fuzzy matching. The address override preserves the
    dashboard's curator assignment.

    1. Resolve all EVM rows found on the RockawayX Dune dashboard.
    2. Confirm co-branded and partner rows are overridden to RockawayX.
    """

    cases = [
        (1, "Tori Ecosystem Vault", "0xcd69123b3FBBfC666E1f6a501da27B564C00De54", "upshift"),
        (1, "Upshift ctUSD", "0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce", "upshift"),
        (1329, "Feather PYUSD0", "0x6137dcfdd3c83fe2922b1cba4105d2e92b327a06", "feather"),
        (1, "Y10K", "0x953972ea0C1703c58F09FB6fD2477Fdcf0FEe074", "ember"),
        (1, "Huma USDC Main", "0x8aC91877b93330f52b2979a31a4879506021475c", "morpho"),
        (1, "RockawayX YIELD USDC", "0xE0181090c22579B6A217f1522cbf8c9f1F0C1965", "morpho"),
        (1, "mROX", "0x67E1F506B148d0Fc95a4E3fFb49068ceB6855c05", "midas"),
        (1, "OnRe Core Vault", "0x0F0a9d3F0bc6006143c96E6995572b51413CB3c4", "accountable"),
        (1, "OnRe Core Vault", "0xb9c317cAE7dd05eCb0c0925020e529934c96f84D", "accountable"),
        (1, "RockawayX wETH", "0x64C18DCC4Ccb3b8D27877a4aeBB4C3126CB39cB9", "morpho"),
        (56, "RockawayX YIELD PT", "0xb5a30e1fa2cf3c8dea882124b3ab5a47a27c5dd2", "lista"),
        (1, "Figure USDC Main", "0xd65d6E8dbC3Cd3D12418199E6f4014dB3aaa0097", "morpho"),
        (1, "RockawayX PRIME USDC", "0x5f829B1B473cBA86838E1B7BB7E144DbDE228e21", "morpho"),
        (1, "Midas USDC Prime (Ethereum)", "0xe99A27169c2aA26a8f2757949d09Fa3f9A8f0B3B", "morpho"),
        (8453, "Midas USDC Prime (Base)", "0xAE4181CFB5aaA08bbE77d269c6B595672b9F9Edc", "morpho"),
    ]

    for chain_id, vault_name, vault_address, protocol_slug in cases:
        slug = identify_curator(
            chain_id=chain_id,
            vault_token_symbol="",
            vault_name=vault_name,
            vault_address=vault_address,
            protocol_slug=protocol_slug,
        )
        assert slug == "rockawayx", f"{vault_name!r} resolved to {slug!r}"


def test_identify_rockawayx_morpho_curator_metadata() -> None:
    """RockawayX Morpho manager metadata resolves to the RockawayX curator."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="Unbranded USDC Vault",
        vault_address="0x0000000000000000000000000000000000000012",
        protocol_slug="morpho",
        manager_name="RockawayX",
    )

    assert slug == "rockawayx"


def test_identify_jpmorgan_jltxx_by_address() -> None:
    """JLTXX resolves to J.P. Morgan by exact contract address."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="JLTXX",
        vault_name="JPMorgan OnChain Liquidity-Token Money Market Fund",
        vault_address="0x09864f52B035AE22eE739dFa5c748fA080D07bD8",
        protocol_slug="kinexys",
    )

    assert slug == "jpmorgan"
    assert get_curator_name("jpmorgan") == "J.P. Morgan"
    assert not is_protocol_curator("jpmorgan")


def test_identify_piku_vaults_by_address() -> None:
    """Resolve the published Piku token and Morini vaults by exact address.

    Piku's branding is not part of every vault name, so the mappings must stay
    address-scoped across Inverter, Accountable and Midas infrastructure.
    """

    cases = [
        ("USP", "USP", "0x098697bA3Fee4eA76294C5d6A466a4e3b3E95FE6", "inverter"),
        ("aFXArbUSDTRY", "Morini FXArbUSDTRY", "0x99351BaEd3d8aB544CCb08aF96A105910fdA71E7", "accountable"),
        ("StockMarketTRBasisTrade", "Morini StockMarketTRBasisTrade Vault", "0x827Ce7E8e35861D9Ac7fE002755767b695A5594a", "midas"),
        ("CarryTradeUSDTRYLeverage", "Morini CarryTradeUSDTRYLeverage Vault", "0x2bf11d2E04Bc40daa95c24B8b90EC4F5c57Dd326", "midas"),
    ]

    for vault_token_symbol, vault_name, vault_address, protocol_slug in cases:
        slug = identify_curator(
            chain_id=1,
            vault_token_symbol=vault_token_symbol,
            vault_name=vault_name,
            vault_address=vault_address,
            protocol_slug=protocol_slug,
        )
        assert slug == "piku", f"{vault_name!r} resolved to {slug!r}"


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
    """Fire Liquidity Provider and renamed FLP vault names resolve to Kappa Lab.

    1. The full "Fire Liquidity Provider" name resolves to Kappa Lab.
    2. The closed Hibachi vault renamed to "FLP - Closed" still resolves via
       the short "FLP" pattern.
    """

    # 1. Full name resolves to Kappa Lab
    slug = identify_curator(
        chain_id=9999,
        vault_token_symbol="FLP",
        vault_name="Fire Liquidity Provider",
        vault_address="hibachi:vault:4",
        protocol_slug="hibachi",
    )

    assert slug == "kappa-lab"

    # 2. Renamed "FLP - Closed" vault still resolves via the short pattern
    slug = identify_curator(
        chain_id=9999,
        vault_token_symbol="FLP",
        vault_name="FLP - Closed",
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


def test_identify_frax_usd_vaults() -> None:
    """Frax USD and frxUSD vault names resolve to Frax Finance."""

    cases = (
        ("euler", "Euler Yield frxUSD"),
        ("frax-finance", "Stable Frax USD Pre-Deposit"),
        ("morpho", "Stake DAO frxUSD V2"),
        ("morpho", "Alpha Frax USD Enhanced V2"),
        ("morpho", "Steakhouse Prime frxUSD"),
    )
    for protocol_slug, vault_name in cases:
        slug = identify_curator(
            chain_id=1,
            vault_token_symbol="",
            vault_name=vault_name,
            vault_address="0x0000000000000000000000000000000000000005",
            protocol_slug=protocol_slug,
        )

        assert slug == "frax-finance", f"{vault_name!r} -> {slug!r}"


def test_identify_frax_usd_vault_name_precedes_manager_name() -> None:
    """Frax-branded vault names win over third-party protocol manager names."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="Steakhouse Prime frxUSD",
        vault_address="0x0000000000000000000000000000000000000005",
        protocol_slug="morpho",
        manager_name="Steakhouse Financial",
    )

    assert slug == "frax-finance"


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


def test_identify_3jane_protocol_curator() -> None:
    """3Jane protocol vaults (USD3/sUSD3) resolve to the protocol-managed slug."""

    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="USD3",
        vault_name="USD3",
        vault_address="0x056B269Eb1f75477a8666ae8C7fE01b64dD55eCc",
        protocol_slug="3jane",
    )

    assert slug == "3jane"
    assert is_protocol_curator("3jane")
    assert get_curator_name("3jane") == "3Jane"


def test_identify_atoma_protocol_curator() -> None:
    """Atoma protocol vaults resolve to the protocol-managed slug."""

    slug = identify_curator(
        chain_id=42161,
        vault_token_symbol="AVS",
        vault_name="Atoma Vault Share",
        vault_address="0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c",
        protocol_slug="atoma",
    )

    assert slug == "atoma"
    assert is_protocol_curator("atoma")
    assert get_curator_name("atoma") == "Atoma"


def test_identify_frankencoin_protocol_curator() -> None:
    """Frankencoin svZCHF vaults resolve to the protocol-managed slug."""

    slug = identify_curator(
        chain_id=100,
        vault_token_symbol="svZCHF",
        vault_name="SavingsVault ZCHF",
        vault_address="0x6165946250dd04740ab1409217e95a4f38374fe9",
        protocol_slug="frankencoin",
    )

    assert slug == "frankencoin"
    assert is_protocol_curator("frankencoin")
    assert get_curator_name("frankencoin") == "Frankencoin"


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
    Lagoon and Kiln.  Most match via their YAML ``name``; Keyring
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
        ("kiln", "Trust Wallet AAVE v3 USDT"): "trust-wallet",
        ("kiln", "Cool Wallet AAVEv3 USDC"): "cool-wallet",
        ("lagoon-finance", "Mt Pelerin - USD strategy pool"): "mt-pelerin",
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

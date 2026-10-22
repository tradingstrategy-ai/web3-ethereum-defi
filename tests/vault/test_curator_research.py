"""Regression coverage for the production R2 curator review of 2026-10-21."""

from pathlib import Path

import pytest
from eth_typing import HexAddress
from PIL import Image

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.autopool.vault import AutoPoolVault
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.erc_4626.vault_protocol.fluid.vault import FluidVault
from eth_defi.erc_4626.vault_protocol.forty_acres.vault import FortyAcresVault
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusVault
from eth_defi.gmx.vault import GMXMarketVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import build_curator_index, identify_curator, is_protocol_curator, load_curator_map
from eth_defi.vault.strategy_tag import StrategyTag

# Fixed expectations from live protocol disclosures, independent of the matcher.
REVIEWED_VAULTS = (
    (1, "0x133614490896bc02c774bc3399e4db1b6d05a2ca", "morpho", "Presto USDC Prime", "presto", ("lending",)),
    (1, "0x68a65f315bcd3c3456d1368ff88bc661856548e1", "morpho", "Presto USDC Forte", "presto", ("lending", "rwa", "rwa_lending")),
    (8453, "0x8effa741061aaa2d8a5012a9b09a2d31d8b628d7", "morpho", "ARCHITECT Global Value", "architect", ("lending",)),
    (8453, "0xb038cf3e233b78225f6d350ac8319bec8457db4a", "morpho", "Birch Hill RWA USDC", "birch-hill", ("lending", "rwa", "rwa_lending")),
    (143, "0x75753e494e5e374c52e1d84fc04eb14b10f2c079", "morpho", "Saturn USDC", "hyperithm", ("lending",)),
    (143, "0xf57e9898575fab80b0f2acde5443276bec8c542d", "morpho", "SharpByte USDC High Yield", "sharpbyte", ("lending", "rwa", "rwa_lending")),
    (1, "0xab5955eb671d150527f8e61a42b703832f86616c", "morpho", "M1 USDC", "waterline-rusd", ("lending",)),
    (1, "0x7d4741ba166b21cf3168a9a0ea71388531c52ff7", "morpho", "Tenbin USDC", "waterline-rusd", ("fx", "lending", "rwa", "rwa_lending")),
    (1, "0xdcd0f5ab30856f28385f641580bbd85f88349124", "lagoon-finance", "Autonomous Liquidity USD", "almanak", ("algorithmic_trading", "lending", "lending_optimisation")),
    (1, "0xcb58582b0d52ce5feecb06ba9ce66598b0d57886", "lagoon-finance", "Strada USDC", "strada-digital", None),
    (8453, "0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4", "lagoon-finance", "GUSDC", "nara-capital", ("delta_neutral", "lending", "liquidity_provider", "multistrategy")),
    (8453, "0xf56bfe07b8d6e6d74258cdb6969a633629b06b08", "lagoon-finance", "MSA - Portefeuille Dynamique ", "gami", ("directional_trading", "discretionary_trading", "multistrategy")),
    (1, "0x71652d4898de9a7dd35e472a5fe4577ec69d82f2", "lagoon-finance", "Trinity Trust", "722-capital", None),
    (1, "0x66dcb62da5430d800a1c807822c25be17138fda8", "lagoon-finance", "Unity Trust", "722-capital", None),
    (1, "0xf3190a3ecc109f88e7947b849b281918c798a0c4", "ember", "Ember Third Eye", "third-eye", ("arbitrage", "carry_trade", "delta_neutral", "multistrategy")),
    (1, "0x9be9294722f8aad37b11a9792be2c782182cafa2", "ember", "Ember Earn", "ember", ("carry_trade", "lending", "liquidity_provider", "multistrategy")),
    (1, "0x9fb7b4477576fe5b32be4c1843afb1e55f251b33", "fluid", "Fluid USD Coin", "fluid", ("lending",)),
    (42161, "0x1a996cb54bb95462040408c06122d45d6cdb6096", "fluid", "Fluid USD Coin", "fluid", ("lending",)),
    (8453, "0xf42f5795d9ac7e9d757db633d693cd548cfd9169", "fluid", "Fluid USD Coin", "fluid", ("lending",)),
    (1, "0x273da948aca9261043fbdb2a857bc255ecc29012", "fluid", "Fluid Lite USD Vault", "fluid", ("lending",)),
    (42161, "0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a", "plutus", "Plutus Hedge Token", "plutus", ("algorithmic_trading", "multistrategy")),
    (42161, "0xf84eaa0685626f84fe17bc6c3c9eb2ac8a90d3c1", "plutus", "Plutus Dolomite vault", "plutus", ("algorithmic_trading",)),
    (1, "0x1abd0403591be494771115d74ed9e120530f356e", "auto-finance", "Anchorage/AUTOfinance anchrgUSD", "auto-finance", ("algorithmic_trading", "liquidity_provider")),
    (1, "0x2916dfb3324377645cac4c5e78dffce910a61393", "auto-finance", "AUTOfinance gpuUSD", "auto-finance", ("algorithmic_trading", "liquidity_provider")),
    (1, "0x408b6a3e2daf288864968454aae786a2a042df36", "auto-finance", "Tokemak siloUSD", "auto-finance", ("algorithmic_trading", "liquidity_provider")),
    (1, "0xa7569a44f348d3d70d8ad5889e50f78e33d80d35", "auto-finance", "Tokemak autoUSD", "auto-finance", ("algorithmic_trading", "liquidity_provider")),
    (1, "0xc7d7f434c015f2a7e77df9763ade300f2171a05a", "auto-finance", "AUTOfinance infinifiUSD", "auto-finance", ("algorithmic_trading", "liquidity_provider")),
    (143, "0x288604ba0e9f08c22815eb36c2f4a49a44ee16bb", "auto-finance", "AUTOfinance monUSD", "auto-finance", ("algorithmic_trading", "liquidity_provider")),
    (42161, "0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c", "auto-finance", "Tokemak arbUSD", "auto-finance", ("algorithmic_trading", "liquidity_provider")),
    (8453, "0x9c6864105aec23388c89600046213a44c384c831", "auto-finance", "Tokemak baseUSD", "auto-finance", ("algorithmic_trading", "liquidity_provider")),
    (8453, "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5", "40acres", "Aerodrome USDC", "40acres", ("lending",)),
    (43114, "0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd", "40acres", "Blackhole USDC", "40acres", ("lending",)),
    (43114, "0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7", "40acres", "Pharaoh USDC", "40acres", ("lending",)),
    (42161, "0x00310c6d8a9f821e3fe991f4835f2ca4d87034cf", "gmx", "GM SKY [WETH-USDC]", "gmx", ("liquidity_provider", "market_making", "market_making_amm", "perpetual_futures")),
    (1, "0xf28dc8b6ded7e45f8cf84b9972487c8e1857a442", "morpho", "syrupUSDC/USDC Morpho Looper", "yearn", ("lending", "lending_looping")),
    (1, "0x59b6c75eeb7dfe417505919c3e5a919b7c1b0773", "morpho", "syrupUSDC/USDC Morpho Looper", "yearn", ("lending", "lending_looping")),
    (1, "0xd362efc75ef1879f37a900823495f402cfdb0986", "morpho", "stcUSD/USDC Pawn Broker Looper", "yearn", ("lending", "lending_looping")),
    (1, "0x2f56d106c6df739bdbb777c2fee79ffaed88d179", "erc-4626", "Arbitrum syrupUSDC/USDC Morpho Looper", "yearn", ("lending", "lending_looping")),
    (1, "0x3d2467cbf82332dbfb38997cbc4d2192694d9490", "erc-4626", "Morpho V2 Sentora PYUSD Convertor", "yearn", ("lending",)),
    (1, "0x7130570bcefcedbe9d15b5b11a33006156460f8f", "erc-4626", "USDC To SKY USDS Depositor", "yearn", ("carry_trade",)),
    (1, "0x9b44efca3e2a707b63dc00ce79d646e5e5d24ba5", "morpho", "USDC Mix Yield Token", "alchemix", ("lending", "multistrategy")),
    (42161, "0xeba62b842081cef5a8184318dc5c4e4aaca9f651", "morpho", "Arbitrum USDC Mix Yield Token", "alchemix", ("lending", "multistrategy")),
    (8453, "0xb8befe5a6941ca4022a52042075ff269c3c67467", "morpho", "USDC Mix Yield Token", "alchemix", ("lending", "multistrategy")),
)


@pytest.mark.parametrize("chain_id,address,protocol,name,curator", [row[:5] for row in REVIEWED_VAULTS])
def test_reviewed_vault_attribution(chain_id: int, address: str, protocol: str, name: str, curator: str) -> None:
    """Resolve every reviewed address even when cached manager metadata is stale."""
    assert identify_curator(chain_id, "", name, address.upper(), protocol, manager_name="Stale manager") == curator
    # The contract address is scoped to its deployment chain, not a global brand.
    assert identify_curator(31337, "", "Unbranded vault", address, protocol) is None


@pytest.mark.parametrize("chain_id,address,protocol,tags", [(row[0], row[1], row[2], row[5]) for row in REVIEWED_VAULTS])
def test_reviewed_strategy_resolvers(chain_id: int, address: str, protocol: str, tags: tuple[str, ...] | None) -> None:
    """Expose the source-backed classifications and protect them from mutation."""
    adapter = {
        "morpho": MorphoV1Vault,
        "lagoon-finance": LagoonVault,
        "ember": EmberVault,
        "fluid": FluidVault,
        "plutus": PlutusVault,
        "40acres": FortyAcresVault,
        "auto-finance": AutoPoolVault,
        "gmx": GMXMarketVault,
        "erc-4626": ERC4626Vault,
    }[protocol]
    vault = object.__new__(adapter)
    vault.spec = VaultSpec(chain_id=chain_id, vault_address=HexAddress(address))
    expected = {StrategyTag(tag) for tag in tags} if tags is not None else None
    actual = vault.get_strategy_tags()
    assert actual == expected
    if actual is not None:
        actual.clear()
        assert vault.get_strategy_tags() == expected


@pytest.mark.parametrize("adapter", (ERC4626Vault, AutoPoolVault, EmberVault, FluidVault, FortyAcresVault, PlutusVault, GMXMarketVault))
def test_unreviewed_strategy_stays_missing(adapter: type) -> None:
    """Native protocol names alone do not establish an individual strategy."""
    vault = object.__new__(adapter)
    vault.spec = VaultSpec(chain_id=1, vault_address=HexAddress("0x0000000000000000000000000000000000000001"))
    assert vault.get_strategy_tags() is None


def test_generic_strategy_is_chain_scoped() -> None:
    """Do not classify an unrelated deployment reusing a Yearn wrapper address."""
    vault = object.__new__(ERC4626Vault)
    vault.spec = VaultSpec(chain_id=8453, vault_address=HexAddress("0x3d2467cbf82332dbfb38997cbc4d2192694d9490"))
    assert vault.get_strategy_tags() is None


@pytest.mark.parametrize(
    "name,protocol,expected",
    (
        ("TelosC Almanak", "ipor-fusion", "telosc"),
        ("Venzo Third Eye", "morpho", None),
        ("Trust Wallet Fluid USDC", "fluid", "trust-wallet"),
        ("RockawayX Ember strategy", "ember", "rockawayx"),
        ("Presto USDC Prime", "morpho", None),
        ("ARCHITECT Global Value II", "morpho", None),
    ),
)
def test_names_do_not_extend_reviewed_scope(name: str, protocol: str, expected: str | None) -> None:
    """Protect sponsors, unrelated operators and unverified branded deployments."""
    assert identify_curator(1, "", name, "0x0000000000000000000000000000000000000001", protocol) == expected


@pytest.mark.parametrize(
    "protocol,manager,expected",
    (
        ("morpho", "Presto", "presto"),
        ("morpho", "Architect", "architect"),
        ("morpho", "Birch Hill", "birch-hill"),
        ("morpho", "Waterline", "waterline-rusd"),
        ("morpho", "Waterline RUSD", "waterline-rusd"),
        ("morpho", "SharpByte", "sharpbyte"),
        ("morpho", "Hyperithm", "hyperithm"),
        ("lagoon-finance", "Nara Capital", "nara-capital"),
        ("lagoon-finance", "722 Capital", "722-capital"),
    ),
)
def test_exact_protocol_manager_names(protocol: str, manager: str, expected: str) -> None:
    """Resolve confirmed metadata labels without requiring a branded vault name."""
    assert identify_curator(1, "", "Unbranded vault", "0x0000000000000000000000000000000000000001", protocol, manager_name=manager) == expected


def test_gami_precedes_msa_for_the_reviewed_portfolio() -> None:
    """Retain the user's explicit single-curator choice for the joint product."""
    assert identify_curator(8453, "", "MSA - Portefeuille Dynamique", "0xf56bfe07b8d6e6d74258cdb6969a633629b06b08", "lagoon-finance", manager_name="MSA, Gami") == "gami"


def test_unreviewed_waterline_deployment_stays_unmatched() -> None:
    """A renamed curator record must not claim another chain's branded vault."""
    assert identify_curator(43114, "", "Waterline USDe", "0xbeeff96ed43f5aefb41af18c72fa2f9eac6dd2ed", "morpho") is None


@pytest.mark.parametrize("slug", ("presto", "architect", "birch-hill", "almanak", "strada-digital", "third-eye", "my-seed-advisor"))
def test_official_curator_artwork_is_usable(slug: str) -> None:
    """Supply non-empty standard PNGs for both interface themes."""
    root = Path(__file__).resolve().parents[2]
    for variant in ("generic", "dark", "light"):
        with Image.open(root / "eth_defi/data/vaults/formatted_logos" / slug / f"{variant}.png") as logo:
            assert logo.size == (256, 256)
            assert logo.format == "PNG"
            assert logo.convert("RGBA").getchannel("A").getbbox() is not None


def test_new_feeders_export_without_duplicates() -> None:
    """Expose native aliases and canonical social sources through the real export."""
    curators = load_curator_map()
    index = build_curator_index(public_url="https://example.invalid")
    slugs = [row["slug"] for row in index]
    assert len(slugs) == len(set(slugs))
    for slug in ("presto", "architect", "birch-hill", "almanak", "strada-digital", "third-eye", "my-seed-advisor", "nara-capital"):
        assert curators[slug]["short_description"]
        assert curators[slug]["long_description"]
        assert not is_protocol_curator(slug)
    for slug in ("fluid", "plutus", "auto-finance", "40acres", "gmx", "ember", "d2-finance"):
        row = next(item for item in index if item["slug"] == slug)
        assert row["protocol_curator"] is True
        assert row["canonical_feeder_id"] == slug
        assert row["short_description"] and row["long_description"]
        assert any(row["logos"].values())
    assert curators["waterline-rusd"]["name"] == "Waterline"
    assert curators["waterline-rusd"]["canonical_feeder_id"] == "steakhouse-financial"

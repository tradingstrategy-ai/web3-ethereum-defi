"""Reviewed Lagoon curator records, stale metadata and strategy coverage."""

from pathlib import Path

import pytest
from PIL import Image

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.feed.sources import load_feeder_metadata
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import build_curator_metadata_json, identify_curator
from eth_defi.vault.strategy_tag import StrategyTag


@pytest.mark.parametrize(("slug", "manager"), [("murmurr", "Murmurr"), ("volta90", "VOLTA90"), ("navi", "NAVI Protocol"), ("stake-capital", "Stake Capital")])
def test_new_lagoon_curator_profile(slug: str, manager: str) -> None:
    """Resolve exact manager metadata and publish a complete curator profile.

    Exporting through the shared schema also checks that the downloaded logo
    is available to the frontend, without requiring live provider requests.

    :param slug: Canonical organisation identifier.
    :param manager: Exact Lagoon manager name.
    :return: ``None`` after checking attribution, descriptions and logo output.
    """
    path = Path("eth_defi/data/feeds/curators") / f"{slug}.yaml"
    data = load_feeder_metadata(path)
    assert data["lagoon-curator"] == manager
    assert identify_curator(1, "", "Unbranded portfolio", "0x0000000000000000000000000000000000000123", "lagoon-finance", manager_name=manager) == slug
    exported = build_curator_metadata_json(path, public_url="https://example.invalid")
    assert exported["short_description"]
    assert exported["long_description"]
    assert exported["website"]
    assert exported["logos"]["generic"] == f"https://example.invalid/curator-metadata/{slug}/generic.png"
    with Image.open(Path("eth_defi/data/vaults/formatted_logos") / slug / "generic.png") as logo:
        assert logo.size == (256, 256)
        assert logo.mode == "RGBA"
        assert logo.getbbox() is not None


@pytest.mark.parametrize(
    ("chain", "address", "slug"),
    [
        (1, "0x7d2c2f54792ad72cb834d298f542145b06b703cb", "murmurr"),
        (1, "0x47491305479635357df29fe0cb6f1af10e51d4da", "navi"),
        (1, "0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8", "volta90"),
        (1, "0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a", "volta90"),
        (42161, "0x9e6de1f581d63129af453fe1ea7253b280b4dca8", "volta90"),
    ],
)
def test_reviewed_lagoon_curator_overrides(chain: int, address: str, slug: str) -> None:
    """Exact deployments override stale names, manager fields and declarations.

    An unrelated same-address deployment must not inherit this attribution.
    The Murmurr case covers metadata from its former DeTrade-labelled product.

    :param chain: Reviewed deployment chain.
    :param address: Reviewed contract address.
    :param slug: Verified current or historical curator.
    :return: ``None`` after checking precedence and chain separation.
    """
    assert identify_curator(chain, "", "DeTrade Morpho X-Chain USDC", address.upper(), "lagoon-finance", manager_name="DeTrade", declared_curator_slug="detrade") == slug
    assert identify_curator(31337, "", "Unbranded vault", address, "lagoon-finance") is None


def test_lagoon_co_curator_keeps_gami_primary() -> None:
    """Adding Stake Capital preserves the reviewed primary curator of lvlUSD.

    The export has one primary curator field. Keeping the upstream order must
    not reassign Gami's vault when the co-curator profile becomes available.

    :return: ``None`` after checking the primary and standalone identities.
    """
    assert identify_curator(1, "", "Gami lvlUSD", "0x59b7942f7d2afd085691ce65c152e0d38d4eff22", "lagoon-finance", manager_name="Gami, Stake Capital") == "gami"


@pytest.mark.parametrize(
    ("chain", "address", "expected"),
    [
        (1, "0x7d2c2f54792ad72cb834d298f542145b06b703cb", "algorithmic_trading lending lending_optimisation"),
        (1, "0x47491305479635357df29fe0cb6f1af10e51d4da", None),
        (1, "0x10dbaf26e61db145c9af764fcd3bdd3993e3ce8a", None),
        (1, "0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8", "algorithmic_trading directional_trading discretionary_trading perpetual_futures"),
        (42161, "0x9e6de1f581d63129af453fe1ea7253b280b4dca8", "algorithmic_trading directional_trading discretionary_trading perpetual_futures"),
        (1, "0x59b7942f7d2afd085691ce65c152e0d38d4eff22", "lending liquidity_provider multistrategy"),
    ],
)
def test_new_curator_vault_strategy_coverage(chain: int, address: str, expected: str | None) -> None:
    """Keep every newly covered vault classified only as its sources warrant.

    The reviewed mappings already cover these strategies. NAVI's exact vault
    lacks strategy text and must not inherit lending from its operator's brand.

    :param chain: Reviewed chain identifier.
    :param address: Exact associated vault contract.
    :param expected: Canonical strategy identifiers, or absent information.
    :return: ``None`` after checking the real adapter hook without RPC.
    """
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain, address)
    assert vault.get_strategy_tags() == ({StrategyTag(tag) for tag in expected.split()} if expected else None)


@pytest.mark.parametrize(
    ("chain", "address"),
    [
        (1, "0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac"),
        (1, "0x29ca54dd3ae9e43abb10880f5e08da439bf25f99"),
        (8453, "0xcda5dbf58a016158ecd4a226109752cc3a627ec8"),
        (143, "0x9f209b43559b13b783eaa0a6686921c7b174f269"),
        (999, "0x3f78bdd48b363ec927dc227d6c80a6e4821c7e10"),
    ],
)
def test_murmurr_morpho_sleeve_coverage(chain: int, address: str) -> None:
    """Cover every lending sleeve linked by Murmurr's current product book.

    Both Morpho adapter generations supply lending automatically, so curator
    onboarding adds no redundant address-specific strategy tags.

    :param chain: Exact chain from the official product link.
    :param address: Exact linked sleeve contract.
    :return: ``None`` after checking curator and adapter strategy resolution.
    """
    assert identify_curator(chain, "", "DeTrade Serenity USDC", address, "morpho") == "murmurr"
    assert identify_curator(31337, "", "Unbranded lending sleeve", address, "morpho") is None
    for adapter in (MorphoV1Vault, MorphoV2Vault):
        vault = object.__new__(adapter)
        vault.spec = VaultSpec(chain, address)
        assert vault.get_strategy_tags() == {StrategyTag.lending}


def test_unverified_volta_name_does_not_establish_curator() -> None:
    """A product name alone cannot establish a relationship to VOLTA90.

    Only exact operator-linked contracts or upstream manager metadata should
    resolve; an arbitrary other contract bearing this name is insufficient.

    :return: ``None`` after checking the unsupported identity stays missing.
    """
    assert identify_curator(1, "", "VOLTA90 MULTI ASSET STRATEGY", "0x0000000000000000000000000000000000000123", "lagoon-finance") is None

"""Verified operator attribution and public curator descriptions."""

from pathlib import Path

import pytest

from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_POOL_ADDRESS
from eth_defi.feed.sources import load_feeder_metadata
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.curator_export import build_curators_for_export


@pytest.mark.parametrize("protocol_slug", ["gains-network", "gtrade"])
def test_nova_operator_corrects_stale_gains_attribution(protocol_slug: str) -> None:
    """Exact identity overrides the shared contract family and stale declarations."""
    assert identify_curator(42161, "nUSDC", "gTrade (Nova USDC)", NOVA_POOL_ADDRESS, protocol_slug, declared_curator_slug="gains-network") == "nova-markets"


@pytest.mark.parametrize(
    ("chain_id", "address", "name"),
    [
        (42161, "0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7", "Gains Network USDC (staging)"),
        (1, NOVA_POOL_ADDRESS, "Nova Markets"),
        (56, "0xfb34af2138280e13b0759fd322fe63fccc7508a6", "Gains Network X-Solaris USD"),
        (42161, "0x000000000000000000000000000000000000f00d", "Gains Network"),
    ],
)
def test_contract_family_does_not_establish_operator(chain_id: int, address: str, name: str) -> None:
    """Names and legacy declared curators do not verify an unknown deployment."""
    assert identify_curator(chain_id, "", name, address, "gains-network", declared_curator_slug="gains-network") is None


def test_gains_name_does_not_attribute_unrelated_protocol() -> None:
    """The Gains organisation name cannot override a different protocol identity."""
    assert identify_curator(1, "GNS", "Gains Network", "0x000000000000000000000000000000000000f00d", "morpho") is None


def test_curator_copy_and_alias_survive_export() -> None:
    """The public export includes researched copy while retaining canonical feeds."""
    curators = build_curators_for_export(["gains-network", "nova-markets"])
    gains = curators["gains-network"]
    assert gains["canonical_feeder_id"] == "gains-network"
    assert gains["protocol_curator"] is True
    assert gains["website"] == "https://gains.trade/"
    assert "January 2021" in gains["long_description"]
    assert "$250,000" in gains["long_description"]
    assert "7 June 2026" in gains["long_description"]
    assert "https://snapshot.org/" in gains["long_description"]
    assert "Conviction Pool" in curators["nova-markets"]["long_description"]
    assert "separate" in curators["nova-markets"]["long_description"]

    feeder_path = Path(__file__).resolve().parents[2] / "eth_defi/data/feeds/curators/gains-network.yaml"
    feeder = load_feeder_metadata(feeder_path)
    assert "twitter" not in feeder
    assert "rss" not in feeder

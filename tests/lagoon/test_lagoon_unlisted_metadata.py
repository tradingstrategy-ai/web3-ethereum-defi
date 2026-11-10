"""Minimal real provider check for unlisted Lagoon metadata without RPC."""

import os
from pathlib import Path

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import fetch_lagoon_metadata_snapshot


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RUN_LAGOON_LIVE") != "true", reason="Set RUN_LAGOON_LIVE=true for the public Lagoon API integration check")
@pytest.mark.skipif(os.environ.get("CI") == "true", reason="Lagoon API returns HTTP 500 for GitHub Actions IPs; see test_lagoon_metadata.py")
def test_unlisted_detail_live(tmp_path: Path) -> None:
    """Verify the real exact-address path succeeds independently of listing.

    This reviewed closed vault has no listing row but supplies strategy text.
    The client validates chain/address in the response, uses temporary storage
    and makes no RPC requests. Source: https://app.lagoon.finance/api/vault.

    :param tmp_path: Isolated disposable provider cache.
    """
    result = fetch_lagoon_metadata_snapshot(1, "0x5a97b0b97197299456af841f8605543b13b12ee3", cache_path=tmp_path)
    assert result.outcome == "success"
    assert result.metadata["name"] == "Autonomous Liquidity Plus"
    assert result.metadata["description"]
    assert result.metadata["short_description"]
    assert result.listed is False

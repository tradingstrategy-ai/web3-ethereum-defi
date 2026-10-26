# Explicit live response shape and valuation tolerance.
# ruff: noqa: PLR2004
"""Opt-in real public app, RPC and trading-account valuation check."""

import os
from pathlib import Path

import pytest

from eth_defi.erc_4626.vault_protocol.infinit.api import fetch_infinit_history
from eth_defi.erc_4626.vault_protocol.infinit.constants import APEX_PRODUCT
from eth_defi.erc_4626.vault_protocol.infinit.events import fetch_infinit_events
from eth_defi.erc_4626.vault_protocol.infinit.vault import InfinitVault
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

pytestmark = pytest.mark.skipif(os.environ.get("INFINIT_LIVE_TEST") != "true", reason="Set INFINIT_LIVE_TEST=true for real external checks")


def test_infinit_live_app_and_equity() -> None:
    """Exercise deployed read-action discovery and native plus xyz market equity."""
    history = fetch_infinit_history()
    assert history.application_id == APEX_PRODUCT.application_id
    assert history.points[0].timestamp < history.points[-1].timestamp
    assert len(history.action_id) >= 40
    web3 = create_multi_provider_web3(read_json_rpc_url(42161))
    vault = InfinitVault(web3, VaultSpec(42161, APEX_PRODUCT.address), token_cache={})
    vault.source_history = history
    validation = vault.fetch_valuation_validation()
    assert validation["trading_equity"] > 0
    assert validation["published_assets"] is not None
    assert validation["relative_difference"] < 0.02


def test_infinit_live_creation(tmp_path: Path) -> None:
    """Use the supplied Hypersync token for one complete deployment block."""
    client = configure_hypersync_from_env(42161).hypersync_client
    assert client is not None
    events = tuple(fetch_infinit_events(client, APEX_PRODUCT, APEX_PRODUCT.deployment_block + 1, tmp_path, start_block=APEX_PRODUCT.deployment_block))
    creations = [event for event in events if event.kind == "created"]
    assert len(creations) == 1
    assert creations[0].transaction_hash == "0xbef5388cf1f6da875e50693ae33632a5d2ad3c366dd6ff802bf7d6d36cf53506"

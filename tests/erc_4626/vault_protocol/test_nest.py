"""Test Nest vault classification and first-party metadata."""

import datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace

import flaky
import pytest
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.nest import offchain_metadata
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import fetch_nest_vaults
from eth_defi.erc_4626.vault_protocol.nest.vault import NestVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import AVALANCHE_MIDNIGHT_BLOCK

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")

# Nest BlackOpal LiquidStone II Vault nOPAL USDC route on Avalanche.
NEST_N_OPAL_AVALANCHE_VAULT = "0xd258029cf5a177e3306e09fbea63424543a505c0"
NEST_N_OPAL_SLUG = "nest-opal-vault"
NEST_N_OPAL_START_BLOCK = 90_379_027
NEST_N_OPAL_REDEMPTION_TIME_DAYS = 4
NEST_N_OPAL_REPORTED_APY = 0.12
NEST_N_OPAL_TVL_USD = 123.45


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create a read-only shared Avalanche fork at the fixed midnight block.

    :param anvil_fork_pool:
        Session-scoped shared Anvil fork pool.

    :return:
        Web3 client connected to the shared Avalanche fork.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_AVALANCHE, AVALANCHE_MIDNIGHT_BLOCK)


# 2026-08-06: Avalanche fork providers may transiently fail; this test passed locally.
@flaky.flaky
@pytest.mark.skipif(JSON_RPC_AVALANCHE is None, reason="JSON_RPC_AVALANCHE needed to run this test")
@pytest.mark.xdist_group("fork:avalanche:midnight")
def test_nest_nopal_vault(web3: Web3) -> None:
    """Identify the nOPAL NestVault using its unique one-call probe.

    ``operatorRegistry()`` is Nest's no-argument classification signal. The
    generic classifier independently identifies the ERC-7540 and ERC-7575
    interfaces exposed by the same contract.

    :param web3:
        Shared fixed-block Avalanche fork client.
    """
    vault = create_vault_instance_autodetect(web3, vault_address=NEST_N_OPAL_AVALANCHE_VAULT)

    assert isinstance(vault, NestVault)
    assert vault.get_protocol_name() == "Nest"
    assert ERC4626Feature.nest_like in vault.features
    assert ERC4626Feature.erc_7540_like in vault.features
    assert ERC4626Feature.erc_7575_like in vault.features
    assert vault.get_deposit_manager_capability() is None

    assert vault.nest_metadata is not None
    assert vault.nest_metadata["symbol"] == "nOPAL"
    assert vault.nest_metadata["slug"] == NEST_N_OPAL_SLUG
    assert vault.description is not None
    assert vault.get_estimated_lock_up().days == NEST_N_OPAL_REDEMPTION_TIME_DAYS
    assert vault.fetch_total_pending_shares() >= 0
    assert vault.get_link() == "https://www.nest.credit/vaults#vaults-explore"


# 2026-08-06: Nest's public APIs are a live external dependency; this test passed locally.
@flaky.flaky
def test_fetch_nest_vaults(tmp_path: Path) -> None:
    """Fetch Nest's first-party API and CMS metadata into a local cache.

    :param tmp_path:
        Isolated filesystem location supplied by pytest.
    """
    vaults = fetch_nest_vaults(cache_path=tmp_path)

    nopal = vaults.get(f"43114:{NEST_N_OPAL_AVALANCHE_VAULT}")
    assert nopal is not None
    assert nopal["name"] == "Nest BlackOpal LiquidStone II Vault"
    assert nopal["asset_symbol"] == "USDC"
    assert nopal["share_token_address"] == "0x119Dd7dAFf816f29D7eE47596ae5E4bdC4299165"
    assert nopal["start_block"] == NEST_N_OPAL_START_BLOCK
    assert nopal["description"] is not None
    assert nopal["redemption_time_days"] == NEST_N_OPAL_REDEMPTION_TIME_DAYS
    assert (tmp_path / "nest_vaults.json").exists()


def test_nest_metadata_parser_joins_contracts_and_cms() -> None:
    """Join the documented first-party API response shapes without network access."""

    metadata = offchain_metadata._parse_nest_vaults(
        [
            {
                "slug": NEST_N_OPAL_SLUG,
                "name": "Nest BlackOpal LiquidStone II Vault",
                "symbol": "nOPAL",
                "vaultAddress": "0x119Dd7dAFf816f29D7eE47596ae5E4bdC4299165",
                "chain": {"avalanche": {"startBlock": NEST_N_OPAL_START_BLOCK}},
                "nestVaults": [
                    {
                        "nestVaultAddress": NEST_N_OPAL_AVALANCHE_VAULT,
                        "asset": "USDC",
                        "chainAssets": [{"chainId": 43114, "assetAddress": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"}],
                    }
                ],
                "sec30d": NEST_N_OPAL_REPORTED_APY,
                "tvl": NEST_N_OPAL_TVL_USD,
            }
        ],
        [
            {
                "slug": NEST_N_OPAL_SLUG,
                "category": {"name": "Yield"},
                "summary": "Short summary",
                "about": "Detailed description",
                "redemptionTime": "4",
                "status": "active",
                "yieldSourcePartners": {"docs": [{"name": "Partner"}]},
            }
        ],
    )

    nopal = metadata[f"43114:{NEST_N_OPAL_AVALANCHE_VAULT}"]
    assert nopal["asset_address"] == "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
    assert nopal["category"] == "Yield"
    assert nopal["yield_source_partners"] == ["Partner"]
    assert nopal["reported_apy"] == NEST_N_OPAL_REPORTED_APY
    assert nopal["tvl_usd"] == NEST_N_OPAL_TVL_USD


def test_nest_metadata_uses_stale_cache_when_api_is_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Retain known route metadata across a temporary first-party API outage."""

    cache_file = tmp_path / "nest_vaults.json"
    cached_vaults = {
        f"43114:{NEST_N_OPAL_AVALANCHE_VAULT}": {
            "name": "Cached nOPAL",
            "vault_address": NEST_N_OPAL_AVALANCHE_VAULT,
        }
    }
    cache_file.write_text(json.dumps(cached_vaults))
    monkeypatch.setattr(offchain_metadata, "_fetch_json", lambda *_args, **_kwargs: None)

    vaults = fetch_nest_vaults(
        cache_path=tmp_path,
        now_=native_datetime_utc_fromtimestamp(cache_file.stat().st_mtime) + datetime.timedelta(days=3),
        max_cache_duration=datetime.timedelta(days=2),
    )

    assert vaults == cached_vaults


def test_nest_metadata_retries_after_initial_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not memoise a temporary metadata outage for the scanner lifetime."""

    key = f"43114:{NEST_N_OPAL_AVALANCHE_VAULT}"
    calls = [{}, {key: {"name": "nOPAL", "vault_address": NEST_N_OPAL_AVALANCHE_VAULT}}]
    monkeypatch.setattr(offchain_metadata, "_cached_vaults", None)
    monkeypatch.setattr(offchain_metadata, "fetch_nest_vaults", lambda: calls.pop(0))
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=43114))

    assert offchain_metadata.fetch_nest_vault_metadata(web3, NEST_N_OPAL_AVALANCHE_VAULT) is None
    assert offchain_metadata.fetch_nest_vault_metadata(web3, NEST_N_OPAL_AVALANCHE_VAULT) is not None

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
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import NEST_ARC_CHAIN_ID, NEST_CURATOR_SLUG, NEST_REVIEWED_MANAGERS, fetch_nest_vaults, select_nest_manager_name
from eth_defi.erc_4626.vault_protocol.nest.tags import STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.nest.vault import NestVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import AVALANCHE_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")
JSON_RPC_ARC = os.environ.get("JSON_RPC_ARC")

# Nest BlackOpal LiquidStone II Vault nOPAL USDC route on Avalanche.
NEST_N_OPAL_AVALANCHE_VAULT = "0xd258029cf5a177e3306e09fbea63424543a505c0"
NEST_ARC_VAULTS = {
    "nest-opal-vault": NEST_N_OPAL_AVALANCHE_VAULT,
    "nest-falconx-clo": "0x4738386d69cf5a7ac088da2887fc0df02795c5e7",
    "plume-factor-vault": "0xb195aebf42c93b50e768a97fd3087bb004b4187f",
}
NEST_N_OPAL_SLUG = "nest-opal-vault"
NEST_N_OPAL_START_BLOCK = 90_379_027
NEST_N_OPAL_REDEMPTION_TIME_DAYS = 4
NEST_N_OPAL_REPORTED_APY = 0.12
NEST_N_OPAL_TARGET_APY = 0.1
NEST_N_OPAL_TVL_USD = 123.45
NEST_N_OPAL_HOLDERS = 100
NEST_N_OPAL_VOLUME_24H = 42.5
REVIEWED_NEST_ENTRYPOINTS = 27


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
    assert vault.get_link() == "https://app.nest.credit/vaults"


@pytest.mark.skipif(JSON_RPC_ARC is None, reason="JSON_RPC_ARC needed to verify live Nest Arc contracts")
def test_nest_arc_vaults_live_provider() -> None:
    """Classify every vault in Nest's current Arc application on the real chain.

    This current-state check avoids an Arc fork and verifies the provider's
    deployed contracts, the first-party CMS fields and direct product links.
    """
    web3 = create_multi_provider_web3(JSON_RPC_ARC)
    assert web3.eth.chain_id == NEST_ARC_CHAIN_ID

    for slug, address in NEST_ARC_VAULTS.items():
        vault = create_vault_instance_autodetect(web3, vault_address=address)
        assert isinstance(vault, NestVault)
        assert ERC4626Feature.nest_like in vault.features
        assert vault.nest_metadata is not None
        assert vault.nest_metadata["slug"] == slug
        assert vault.nest_metadata["start_block"] is not None
        assert vault.nest_metadata["yield_origin"]
        assert vault.nest_metadata["risk_summary"]
        assert vault.name == vault.nest_metadata["display_name"]
        assert vault.get_link() == f"https://app.nest.credit/vaults/{slug}"
        expected_manager = {
            "nest-opal-vault": "BlackOpal",
            "nest-falconx-clo": "M11 Credit",
            "plume-factor-vault": "Plume",
        }[slug]
        assert vault.manager_name == expected_manager
        assert vault.fetch_scan_record_extra_data()["_curator_slug"] == NEST_CURATOR_SLUG
        assert vault.get_strategy_tags() == lookup_strategy_tags(STRATEGY_TAGS, address)


@pytest.mark.parametrize(
    ("chain_id", "rpc_env", "slug", "address"),
    [
        (8453, "JSON_RPC_BASE", "nest-alpha-vault", "0x0342ee795e7864319fb8d48651b47febf1163c34"),
        (143, "JSON_RPC_MONAD", "nest-opal-vault", NEST_N_OPAL_AVALANCHE_VAULT),
        (480, "JSON_RPC_WORLDCHAIN", "nest-alpha-vault", "0x0342ee795e7864319fb8d48651b47febf1163c34"),
        (4663, "JSON_RPC_ROBINHOOD", "nest-opal-vault", "0x437157515e274e8b150a52d9d1fabcf701529b8f"),
        (98866, "JSON_RPC_PLUME", "nest-alpha-vault", "0x0342ee795e7864319fb8d48651b47febf1163c34"),
    ],
)
def test_nest_new_chains_live_provider(chain_id: int, rpc_env: str, slug: str, address: str) -> None:
    """Verify the new Nest probe chains at their latest available state.

    Monad has no archive-complete state, so the test reads current state only.

    :param chain_id:
        Verified EVM chain ID.
    :param rpc_env:
        Configured RPC environment variable name.
    :param slug:
        First-party Nest product slug.
    :param address:
        Chain-specific NestVault entrypoint.
    """
    if not (rpc_url := os.environ.get(rpc_env)):
        pytest.skip(f"{rpc_env} needed to verify live Nest contracts")

    web3 = create_multi_provider_web3(rpc_url)
    assert web3.eth.chain_id == chain_id
    vault = create_vault_instance_autodetect(web3, vault_address=address)
    assert isinstance(vault, NestVault)
    assert ERC4626Feature.nest_like in vault.features
    assert vault.nest_metadata is not None
    assert vault.nest_metadata["slug"] == slug
    assert vault.nest_metadata["start_block"] is not None
    assert vault.get_link() == "https://app.nest.credit/vaults"


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
    assert nopal["yield_origin"]
    assert nopal["risk_summary"]
    assert nopal["num_holders"] is not None
    assert nopal["redemption_time_days"] == NEST_N_OPAL_REDEMPTION_TIME_DAYS
    for slug, address in NEST_ARC_VAULTS.items():
        route = vaults.get(f"5042:{address}")
        assert route is not None
        assert route["slug"] == slug
        assert route["start_block"] is not None
    for route in vaults.values():
        if route["status"] == "active" and route["slug"] in NEST_REVIEWED_MANAGERS:
            assert route["vault_address"].lower() in STRATEGY_TAGS
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
                "targetApy": NEST_N_OPAL_TARGET_APY,
                "tvl": NEST_N_OPAL_TVL_USD,
                "numHolders": NEST_N_OPAL_HOLDERS,
                "volume24h": NEST_N_OPAL_VOLUME_24H,
            }
        ],
        [
            {
                "slug": NEST_N_OPAL_SLUG,
                "category": {"name": "Yield"},
                "summary": "Short summary",
                "about": "Detailed description",
                "name": "BlackOpal LiquidStone II",
                "symbol": "nOPAL",
                "yieldOrigin": "Short-duration receivables",
                "riskSummary": "Borrowers may default",
                "underlyingRisks": "Servicing may fail",
                "estimatedApy": ".10",
                "redemptionTime": "4",
                "status": "active",
                "yieldSourcePartners": {"docs": [{"name": "Partner"}]},
            }
        ],
    )

    nopal = metadata[f"43114:{NEST_N_OPAL_AVALANCHE_VAULT}"]
    assert nopal["asset_address"] == "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
    assert nopal["category"] == "Yield"
    assert nopal["display_name"] == "BlackOpal LiquidStone II"
    assert nopal["yield_origin"] == "Short-duration receivables"
    assert nopal["risk_summary"] == "Borrowers may default"
    assert nopal["underlying_risks"] == "Servicing may fail"
    assert nopal["yield_source_partners"] == ["Partner"]
    assert nopal["reported_apy"] == NEST_N_OPAL_REPORTED_APY
    assert nopal["target_apy"] == pytest.approx(NEST_N_OPAL_TARGET_APY)
    assert nopal["estimated_apy"] == pytest.approx(NEST_N_OPAL_TARGET_APY)
    assert nopal["tvl_usd"] == NEST_N_OPAL_TVL_USD
    assert nopal["num_holders"] == NEST_N_OPAL_HOLDERS
    assert nopal["volume_24h_usd"] == NEST_N_OPAL_VOLUME_24H


def test_nest_manager_selection_and_curator_guard() -> None:
    """Distinguish reviewed curators from a CMS list of asset suppliers."""
    assert select_nest_manager_name("nest-opal-vault", ["Superstate", "BlackOpal"]) == "BlackOpal"
    assert select_nest_manager_name("nest-falconx-clo", ["M11 Credit", "FalconX"]) == "M11 Credit"
    assert select_nest_manager_name("plume-factor-vault", []) == "Plume"
    assert select_nest_manager_name("nest-basis-vault", []) == "Bitwise"
    assert select_nest_manager_name("nprime", []) == "Hastra"
    assert select_nest_manager_name("nest-onre-vault", ["OnRe"]) == "OnRe"
    assert select_nest_manager_name("nest-acrdx-vault", ["Apollo", "Centrifuge"]) == "Apollo"
    assert select_nest_manager_name("nest-axi-vault", []) is None
    assert identify_curator(5042, "nACRDX", "Apollo credit", NEST_ARC_VAULTS["plume-factor-vault"], "nest", manager_name="Apollo") is None
    assert identify_curator(5042, "nOPAL", "BlackOpal", NEST_ARC_VAULTS["nest-opal-vault"], "nest", manager_name="BlackOpal", declared_curator_slug=NEST_CURATOR_SLUG) == NEST_CURATOR_SLUG


def test_nest_reader_retains_small_nav_changes() -> None:
    """Retain sampled Nest NAVs in both migration and recurring readers."""
    vault = NestVault(Web3(), VaultSpec(43114, NEST_N_OPAL_AVALANCHE_VAULT))
    assert vault.get_historical_reader(stateful=False).write_all_samples is True
    assert vault.get_historical_reader(stateful=True).write_all_samples is True


def test_reviewed_nest_strategy_tags() -> None:
    """Every route for a newly reviewed curator has source-maintained tags."""
    assert len(STRATEGY_TAGS) == REVIEWED_NEST_ENTRYPOINTS
    assert lookup_strategy_tags(STRATEGY_TAGS, NEST_ARC_VAULTS["nest-opal-vault"]) == {StrategyTag.rwa, StrategyTag.rwa_credit}
    assert lookup_strategy_tags(STRATEGY_TAGS, NEST_ARC_VAULTS["nest-falconx-clo"]) == {StrategyTag.lending}
    assert lookup_strategy_tags(STRATEGY_TAGS, NEST_ARC_VAULTS["plume-factor-vault"]) == {StrategyTag.rwa, StrategyTag.rwa_credit}
    assert lookup_strategy_tags(STRATEGY_TAGS, "0x0000000000000000000000000000000000000001") is None


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

    with pytest.raises(RuntimeError, match="refusing to migrate"):
        fetch_nest_vaults(
            cache_path=tmp_path,
            now_=native_datetime_utc_fromtimestamp(cache_file.stat().st_mtime) + datetime.timedelta(days=3),
            max_cache_duration=datetime.timedelta(days=2),
            allow_stale=False,
        )


def test_nest_migration_requires_cms_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A working contract API cannot mask a failed CMS during a migration."""
    monkeypatch.setattr(offchain_metadata, "_fetch_json", lambda url, **_kwargs: {"data": [{"slug": "example"}]} if "cms.nest.credit" not in url else None)
    with pytest.raises(RuntimeError, match="incomplete first-party data"):
        fetch_nest_vaults(cache_path=tmp_path, allow_stale=False)


def test_fresh_catalogue_replaces_adapter_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Use a migration's fresh CMS data even if an adapter was loaded earlier."""
    key = f"43114:{NEST_N_OPAL_AVALANCHE_VAULT}"
    fresh = {key: {"name": "nOPAL", "description": "Updated strategy", "redemption_time_days": 4}}
    monkeypatch.setattr(offchain_metadata, "_cached_vaults", {key: {"description": "Old strategy"}})
    monkeypatch.setattr(offchain_metadata, "_fetch_json", lambda *_args, **_kwargs: {"data": [{}], "docs": [{}]})
    monkeypatch.setattr(offchain_metadata, "_parse_nest_vaults", lambda *_args: fresh)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=43114))

    fetch_nest_vaults(cache_path=tmp_path, allow_stale=False)
    metadata = offchain_metadata.fetch_nest_vault_metadata(web3, NEST_N_OPAL_AVALANCHE_VAULT)

    assert metadata["description"] == "Updated strategy"
    assert metadata["redemption_time_days"] == NEST_N_OPAL_REDEMPTION_TIME_DAYS


def test_cms_outage_preserves_cached_descriptions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Do not overwrite saved descriptions when only the contract API works."""
    cached = {"example": {"description": "Keep this strategy", "status": "active"}}
    cache_file = tmp_path / "nest_vaults.json"
    cache_file.write_text(json.dumps(cached))
    monkeypatch.setattr(offchain_metadata, "_fetch_json", lambda url, **_kwargs: {"data": [{"slug": "example"}]} if "cms.nest.credit" not in url else None)

    assert fetch_nest_vaults(cache_path=tmp_path, max_cache_duration=datetime.timedelta(0)) == cached
    assert json.loads(cache_file.read_text()) == cached


def test_initial_cms_outage_does_not_cache_partial_catalogue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Retry a missing CMS on the next read instead of caching incomplete rows."""
    monkeypatch.setattr(offchain_metadata, "_cached_vaults", None)
    monkeypatch.setattr(offchain_metadata, "_fetch_json", lambda url, **_kwargs: {"data": [{"slug": "example"}]} if "cms.nest.credit" not in url else None)

    assert fetch_nest_vaults(cache_path=tmp_path) == {}
    assert offchain_metadata._cached_vaults is None
    assert not (tmp_path / "nest_vaults.json").exists()


def test_nest_metadata_retries_after_initial_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not memoise a temporary metadata outage for the scanner lifetime."""

    key = f"43114:{NEST_N_OPAL_AVALANCHE_VAULT}"
    calls = [{}, {key: {"name": "nOPAL", "vault_address": NEST_N_OPAL_AVALANCHE_VAULT}}]
    monkeypatch.setattr(offchain_metadata, "_cached_vaults", None)
    monkeypatch.setattr(offchain_metadata, "fetch_nest_vaults", lambda: calls.pop(0))
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=43114))

    assert offchain_metadata.fetch_nest_vault_metadata(web3, NEST_N_OPAL_AVALANCHE_VAULT) is None
    assert offchain_metadata.fetch_nest_vault_metadata(web3, NEST_N_OPAL_AVALANCHE_VAULT) is not None

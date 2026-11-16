"""Curator, strategy and description provenance through native metadata/export."""

import copy
import datetime
from pathlib import Path

import pytest

from eth_defi.neutral_trade.constants import SOLANA_DATASET_CHAIN_ID, make_solana_vault_address
from eth_defi.neutral_trade.tags import get_strategy_tags
from eth_defi.neutral_trade.vault_data_export import create_vault_row
from eth_defi.neutral_trade.vault_metadata import NATIVE_VAULT_METADATA
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import get_curator_name, identify_curator
from eth_defi.vault.curator_export import build_curators_for_export
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.top_vaults_json import _find_stale_post_processed_vault_ids  # noqa: PLC2701
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

# ruff: noqa: DTZ001 - dataset timestamps are naive UTC


@pytest.mark.parametrize(
    ("address", "curator_name"),
    [
        ("3vZKAcd74bzwNYZmsJndYExkGj6ABQwrUoiMtdNfzLZe", "Xanthas"),
        ("46gSEJLNTdDFq7DPjH8RbcTmG1gyw4JCKCr1XfuAfYvh", "AdverseGuard Alpha"),
        ("4QBTzqUbn1crLg2PDUGt8abJfqHjeuZkjNLjCCit69ui", "D Research"),
        ("Aea2VZfX6hw758cxx2WaGm7kqmdVjhbYsLfde5chVG1W", "AdverseGuard Alpha"),
        ("C68A4mAhA9EE4rWq9HmFnq3SPcbNmst6qiBWcna5VDHy", "Vision Research"),
        ("De47QBuMP7xukYBek5r4ScZF4HEk1kHW34ymKASz3DLt", "Velox Trading Group"),
        ("DzmixPGgru86YkQY4LSEuM1Sfz7ZFPc1wHwSm4fejXbJ", "AdverseGuard Alpha"),
        ("Ec6uSz5EsffwfXp43pvpz4rf9ttJRFKES4ZSVX7gVVkQ", "Hyperithm"),
        ("Eo3m78EQcHnwMyHtxaiz1vw4nwHa85RuGE8jLsrELhxj", "Atlas Research"),
        ("G5aMxQTbGWMnYycpfjHpD7Y1muoKBwaB1HtCdpUUcQZp", "C Research"),
        ("HWjMfYfc7KoPchoEDa5UFyVSpJxY3aV1RAccKvABYb9z", "Neutral Trade"),
        ("J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ", "Neutral Trade"),
        ("nE1x7KQq2sm3GQrafQUUdBkSPPT52FmiMM9qAS1dgnC", "Neutral Trade"),
    ],
)
def test_reviewed_native_metadata(address: str, curator_name: str) -> None:
    """Resolve all reviewed accounts without using names or the shared wallet.

    Deliberately misleading provider names must not replace reviewed account
    attribution. Descriptions and strategy tags must populate individual rows,
    while curator short/long descriptions load through the shared export.

    :param address: Exact native account reviewed in the official registry.
    :param curator_name: Independently enumerated expected public curator name.
    :return: ``None`` after checking attribution, descriptions and tag isolation.
    """
    metadata = NATIVE_VAULT_METADATA[address]
    row = make_row(address, "Hyperliquid Autopilot")
    slug = identify_curator(SOLANA_DATASET_CHAIN_ID, "fixture", row["Name"], row["Address"], "neutral-trade", row["_manager_name"], row["_curator_slug"])
    assert get_curator_name(slug) == curator_name
    assert row["_manager_name"] == curator_name
    assert row["_description"] and row["_short_description"]
    assert "\n" not in row["_short_description"]
    assert row["_neutral_trade"]["metadata_source"] == metadata.source_url
    assert row["_neutral_trade"]["manager"] == "2VYioVd8gaE1Zp1sMbo1u48pSLSnBsX6da4xxQs1MAMp"
    profile = build_curators_for_export([slug])[slug]
    assert profile["name"] == curator_name
    assert profile["short_description"] and profile["long_description"]
    tags = get_strategy_tags(row["Address"])
    assert tags and row["_strategy_tags"] == tags
    tags.clear()
    assert get_strategy_tags(row["Address"])


def make_row(address: str, name: str) -> dict:
    """Build a source-shaped row through the production native exporter.

    The common manager wallet intentionally carries no curator identity;
    different vault accounts must still resolve to distinct trading teams.

    :param address: Case-sensitive native vault account.
    :param name: Mutable provider display name, which may be misleading.
    :return: Native shared metadata row with synthetic timestamp and fee data.
    """
    raw = {"bundleKey": address, "chain": "solana", "name": name, "manager": "2VYioVd8gaE1Zp1sMbo1u48pSLSnBsX6da4xxQs1MAMp", "visible": True, "private": False, "asset": {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC", "decimals": 6}}
    record = {"raw": raw, "chain": "solana", "skip_reason": None, "metrics": None, "first_seen": datetime.datetime(2026, 11, 1), "last_seen": datetime.datetime(2026, 11, 12)}
    return create_vault_row(record)[1]


@pytest.mark.parametrize("name", ["Neutral Autopilot", "Hyperliquid Funding Arb", "Xanthas"])
def test_unknown_native_identity_is_not_guessed(name: str) -> None:
    """Leave unknown accounts empty instead of assigning a matching venue brand.

    A shared manager, familiar name or curator alias does not establish
    ownership of an unreviewed account, even when descriptions exist elsewhere.

    :param name: Display names that would otherwise match unrelated curators.
    :return: ``None`` after checking explicit missing-information results.
    """
    row = make_row("11111111111111111111111111111111", name)
    assert row["_description"] is None and row["_short_description"] is None
    assert row["_manager_name"] is None and not row.get("_curator_slug")
    assert row["_strategy_tags"] is None
    assert identify_curator(SOLANA_DATASET_CHAIN_ID, "", name, row["Address"], "neutral-trade") is None


def test_lending_and_equity_vaults_do_not_inherit_perpetual_futures() -> None:
    """Keep native strategy classification specific to the documented instruments.

    The native pipeline covers lending and listed equities as well as crypto
    trading; its protocol identity cannot imply perpetual exposure.

    :return: ``None`` after comparing the reviewed non-perpetual tag sets.
    """
    earn = get_strategy_tags(make_solana_vault_address("HWjMfYfc7KoPchoEDa5UFyVSpJxY3aV1RAccKvABYb9z"))
    assert earn == {StrategyTag.algorithmic_trading, StrategyTag.lending, StrategyTag.lending_optimisation}
    equities = get_strategy_tags(make_solana_vault_address("Ec6uSz5EsffwfXp43pvpz4rf9ttJRFKES4ZSVX7gVVkQ"))
    assert StrategyTag.rwa in equities and StrategyTag.perpetual_futures not in equities


def test_editorial_refresh_preview_apply_and_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh cached metadata without changing fees, visibility or historical files.

    Include all reviewed native rows, quarantined metadata, an unknown native
    account and an Accountable sentinel. The runner must preserve preview bytes,
    retain backups and skip an already successful application.

    :param tmp_path: Isolated metadata, history sentinels and migration resources.
    :param monkeypatch: Remove legacy path overrides from the private environment.
    :return: ``None`` after preview, apply and retry preservation checks.
    """
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    data, scripts = tmp_path / "data", tmp_path / "scripts"
    data.mkdir()
    scripts.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == "068-neutral-trade-curators-and-descriptions.py")
    (scripts / script.name).write_bytes(script.source)
    rows = {}
    for address, metadata in NATIVE_VAULT_METADATA.items():
        row = make_row(address, metadata.name)
        row.update(_description=None, _short_description=None, _manager_name=None, _curator_slug="ipor", _strategy_tags=None)
        row["_neutral_trade"].update(visible=False, history_verified=False)
        row["_neutral_trade"].pop("metadata_source")
        rows[VaultSpec(SOLANA_DATASET_CHAIN_ID, row["Address"])] = row
    unknown = VaultSpec(SOLANA_DATASET_CHAIN_ID, make_solana_vault_address("11111111111111111111111111111111"))
    evm = VaultSpec(1, "0x" + "12" * 20)
    rows[unknown] = {"Protocol": "Neutral Trade", "_description": "Preserve unknown account"}
    rows[evm] = {"Protocol": "Accountable", "_description": "Preserve EVM metadata"}
    original_rows = copy.deepcopy(rows)
    path = data / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows, last_scanned_block={1: 123}).write(path)
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "neutral-trade-vaults.duckdb"):
        (data / name).write_bytes(b"Preserve historical data")
    original = {file.name: file.read_bytes() for file in data.iterdir()}
    options = {"data_dir": data, "scripts_dir": scripts, "provenance": {"in_docker": False}}
    runner.run_pending_migrations(dry_run=True, **options)
    assert {file.name: file.read_bytes() for file in data.iterdir()} == original
    runner.run_pending_migrations(dry_run=False, **options)
    result = VaultDatabase.read(path)
    assert result.last_scanned_block == {1: 123}
    assert result.rows[unknown] == original_rows[unknown]
    assert result.rows[evm] == original_rows[evm]
    for address, metadata in NATIVE_VAULT_METADATA.items():
        spec = VaultSpec(SOLANA_DATASET_CHAIN_ID, make_solana_vault_address(address))
        row = result.rows[spec]
        assert row["_curator_slug"] == metadata.curator_slug
        assert row["_description"] and row["_short_description"] and row["_strategy_tags"]
        assert row["_neutral_trade"]["visible"] is False
        assert row["_neutral_trade"]["history_verified"] is False
        assert row["_fees"] == original_rows[spec]["_fees"]
    assert all((data / name).read_bytes() == content for name, content in original.items() if name != path.name)
    applied = {file.name: file.read_bytes() for file in data.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data.iterdir()} == applied


def test_sticky_editorial_refresh_bypasses_freshness_only_when_needed() -> None:
    """Refresh stale native descriptions and attribution once, keeping other caches.

    Even recently calculated low-TVL metrics must not replay an old curator
    after a metadata migration. Once repaired, normal scheduling resumes.

    :return: ``None`` after comparing stale and corrected persisted records.
    """
    address = "J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ"
    row = make_row(address, "Neutral Autopilot")
    spec = VaultSpec(SOLANA_DATASET_CHAIN_ID, row["Address"])
    database = VaultDatabase(rows={spec: row})
    record = {"protocol": "Neutral Trade", "protocol_slug": "neutral-trade", "link": row["Link"], "features": sorted(feature.name for feature in row["_detection_data"].features), "curator_slug": "ipor", "description": None, "short_description": None}
    state = {"vaults": {spec.as_string_id(): {"last_exported_record": record}}}
    assert _find_stale_post_processed_vault_ids(database, set(), state) == {spec.as_string_id()}
    record.update(curator_slug=row["_curator_slug"], description=row["_description"], short_description=row["_short_description"])
    assert _find_stale_post_processed_vault_ids(database, set(), state) == set()
    record["short_description"] = "Old short summary."
    assert _find_stale_post_processed_vault_ids(database, set(), state) == {spec.as_string_id()}
    unrelated = VaultSpec(1, "0x" + "12" * 20)
    database.rows[unrelated] = {"Protocol": "Accountable"}
    state["vaults"][unrelated.as_string_id()] = {"last_exported_record": {"curator_slug": "Keep curator"}}
    assert unrelated.as_string_id() not in _find_stale_post_processed_vault_ids(database, set(), state)

"""Offline tests for Xerberus vault export helpers."""

import datetime
import json
from pathlib import Path

import pytest

from eth_defi.xerberus.constants import XERBERUS_SCORE_SCALE
from eth_defi.xerberus.database import XerberusDatabase
from eth_defi.xerberus.vault_export import (
    build_xerberus_dendrogram_url,
    build_xerberus_pool_lookup,
    build_xerberus_protocols_for_export,
    compute_xerberus_export_stats,
    resolve_xerberus_vault_section,
)


@pytest.fixture()
def db(tmp_path: Path):
    database = XerberusDatabase(tmp_path / "export.duckdb")
    yield database
    database.close()


def test_compute_stats():
    vaults = [
        {"xerberus": {"entity_type": "pool", "score": 1}},
        {"xerberus": {"entity_type": "protocol", "score": 2}},
        {"xerberus": None},
        {},
    ]
    stats = compute_xerberus_export_stats(vaults)
    assert stats["total_vaults"] == 4
    assert stats["pool_matches"] == 1
    assert stats["protocol_fallbacks"] == 1
    assert stats["unmatched"] == 2
    assert stats["coverage_pct"] == 50.0


def test_score_scale_always_set(db: XerberusDatabase):
    fetched_at = datetime.datetime(2026, 7, 25, 12, 0, 0)
    db.insert_registry_snapshot_batch(
        [
            {
                "type": "pool",
                "id": "pool_z",
                "name": "Z",
                "chain": "ethereum",
                "address": "0xdddddddddddddddddddddddddddddddddddddddd",
                "chainId": 1,
                "score": 12,
            }
        ],
        fetched_at,
    )
    pools = build_xerberus_pool_lookup(db, max_age_days=None)
    section = resolve_xerberus_vault_section(
        1,
        "0xdddddddddddddddddddddddddddddddddddddddd",
        None,
        pools,
        {},
    )
    assert section is not None
    assert section["score_scale"] == XERBERUS_SCORE_SCALE


def test_build_xerberus_dendrogram_url_escapes_entity_id() -> None:
    """Build no URL for a blank id and escape reserved app-path characters."""
    assert build_xerberus_dendrogram_url("pool", "  ") is None
    assert build_xerberus_dendrogram_url("protocol", "morpho/v1") == "https://app.xerberus.io/protocol/dendrogram/morpho%2Fv1"


def test_exported_pool_and_protocol_ratings_include_json_backlinks(db: XerberusDatabase):
    """Export stable Xerberus app URLs for both pool and protocol ratings."""
    fetched_at = datetime.datetime(2026, 7, 25, 12, 0, 0)
    address = "0xdddddddddddddddddddddddddddddddddddddddd"
    db.insert_registry_snapshot_batch(
        [
            {
                "type": "pool",
                "id": "ipor-dai-vault",
                "name": "IPOR DAI Prime",
                "chain": "ethereum",
                "address": address,
                "chainId": 1,
                "score": 83,
            },
            {
                "type": "protocol",
                "id": "morpho-v1",
                "name": "Morpho",
                "chain": None,
                "address": None,
                "chainId": None,
                "score": 66,
            },
        ],
        fetched_at,
    )
    vault_list_address = "0xffffffffffffffffffffffffffffffffffffffff"
    db.insert_vault_list_snapshot_batch(
        "morpho",
        [
            {
                "id": "morpho-v2",
                "name": "Morpho vault list entry",
                "chain": "ethereum",
                "address": vault_list_address,
                "chainId": 1,
                "score": 71,
            }
        ],
        fetched_at,
    )
    db.upsert_report_url(
        1,
        vault_list_address,
        "https://app.xerberus.io/pool/dendrogram/morpho-vault-list-entry",
        fetched_at,
    )

    pools = build_xerberus_pool_lookup(db, max_age_days=None)
    protocols = build_xerberus_protocols_for_export(db, ["morpho"], max_age_days=None)
    pool_section = resolve_xerberus_vault_section(1, address, None, pools, protocols)
    protocol_section = resolve_xerberus_vault_section(1, "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "morpho", pools, protocols)
    vault_list_section = resolve_xerberus_vault_section(1, vault_list_address, None, pools, protocols)

    assert pool_section is not None
    assert pool_section["report_url"] == "https://app.xerberus.io/pool/dendrogram/ipor-dai-vault"
    assert protocols["morpho"]["report_url"] == "https://app.xerberus.io/protocol/dendrogram/morpho-v1"
    assert protocol_section is not None
    assert protocol_section["report_url"] == protocols["morpho"]["report_url"]
    assert vault_list_section is not None
    assert vault_list_section["report_url"] == "https://app.xerberus.io/pool/dendrogram/morpho-vault-list-entry"

    json.dumps(
        {
            "xerberus_protocols": protocols,
            "vaults": [
                {"xerberus": pool_section},
                {"xerberus": protocol_section},
                {"xerberus": vault_list_section},
            ],
        },
        allow_nan=False,
    )

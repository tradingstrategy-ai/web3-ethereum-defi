"""Offline tests for Xerberus vault export helpers."""

import datetime
from pathlib import Path

import pytest

from eth_defi.xerberus.constants import XERBERUS_SCORE_SCALE
from eth_defi.xerberus.database import XerberusDatabase
from eth_defi.xerberus.vault_export import (
    build_xerberus_pool_lookup,
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

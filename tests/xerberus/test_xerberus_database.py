"""Offline tests for XerberusDatabase — no API key required."""

import datetime
from pathlib import Path

import pytest

from eth_defi.xerberus.constants import XERBERUS_SCORE_SCALE
from eth_defi.xerberus.database import XerberusDatabase
from eth_defi.xerberus.vault_export import (
    build_xerberus_pool_lookup,
    build_xerberus_protocols_for_export,
    resolve_xerberus_vault_section,
)


@pytest.fixture()
def db(tmp_path: Path):
    database = XerberusDatabase(tmp_path / "test.duckdb")
    yield database
    database.close()


def test_insert_registry_and_casefold_lookup(db: XerberusDatabase):
    fetched_at = datetime.datetime(2026, 7, 25, 12, 0, 0)
    entities = [
        {
            "type": "pool",
            "id": "pool_abc",
            "name": "Test Vault",
            "chain": "ethereum",
            "address": "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
            "chainId": 1,
            "score": 55,
        },
        {
            "type": "protocol",
            "id": "proto_morpho",
            "name": "Morpho",
            "chain": None,
            "address": None,
            "chainId": None,
            "score": 84,
        },
    ]
    assert db.insert_registry_snapshot_batch(entities, fetched_at) == 2

    row = db.get_latest_pool_score(1, "0xabcdef0123456789abcdef0123456789abcdef01", max_age_days=None)
    assert row is not None
    assert row["score"] == 55
    assert row["address"] == "0xabcdef0123456789abcdef0123456789abcdef01"
    assert row["entity_id"] == "pool_abc"

    # Mixed-case lookup
    row2 = db.get_latest_pool_score(1, "0xABCDEF0123456789ABCDEF0123456789ABCDEF01", max_age_days=None)
    assert row2 is not None
    assert row2["entity_id"] == "pool_abc"


def test_skip_pool_missing_address(db: XerberusDatabase):
    fetched_at = datetime.datetime(2026, 7, 25, 12, 0, 0)
    n = db.insert_registry_snapshot_batch(
        [{"type": "pool", "id": "pool_bad", "name": "Bad", "chainId": 1, "score": 1}],
        fetched_at,
    )
    assert n == 0


def test_score_daily_same_day_idempotent(db: XerberusDatabase):
    day = datetime.date(2026, 7, 25)
    fetched_1 = datetime.datetime(2026, 7, 25, 10, 0, 0)
    fetched_2 = datetime.datetime(2026, 7, 25, 18, 0, 0)
    db.upsert_score_daily_points(
        [
            {
                "entity_type": "pool",
                "entity_id": "pool_abc",
                "day": day,
                "score": 40,
                "fetched_at": fetched_1,
                "chain_id": 1,
                "address": "0xabc",
            }
        ]
    )
    db.upsert_score_daily_points(
        [
            {
                "entity_type": "pool",
                "entity_id": "pool_abc",
                "day": day,
                "score": 60,
                "fetched_at": fetched_2,
                "chain_id": 1,
                "address": "0xabc",
            }
        ]
    )
    counts = db.get_entity_counts()
    assert counts["score_daily"] == 1
    with db._db_lock:
        score = db.con.execute("SELECT score FROM score_daily WHERE entity_id = 'pool_abc'").fetchone()[0]
    assert score == 60


def test_pool_lookup_merge_registry_wins_over_vault_list(db: XerberusDatabase):
    fetched_at = datetime.datetime(2026, 7, 25, 12, 0, 0)
    db.insert_registry_snapshot_batch(
        [
            {
                "type": "pool",
                "id": "pool_reg",
                "name": "Reg",
                "chain": "base",
                "address": "0x1111111111111111111111111111111111111111",
                "chainId": 8453,
                "score": 70,
            }
        ],
        fetched_at,
    )
    db.insert_vault_list_snapshot_batch(
        "morpho",
        [
            {
                "id": "morpho-v1",
                "name": "List",
                "chain": "base",
                "address": "0x1111111111111111111111111111111111111111",
                "chainId": 8453,
                "score": 10,
            },
            {
                "id": "morpho-v2",
                "name": "List Only",
                "chain": "base",
                "address": "0x2222222222222222222222222222222222222222",
                "chainId": 8453,
                "score": 33,
            },
        ],
        fetched_at,
    )

    lookup = build_xerberus_pool_lookup(db, max_age_days=None)
    reg = lookup[(8453, "0x1111111111111111111111111111111111111111")]
    assert reg["score"] == 70
    assert reg["source"] == "registry"
    only = lookup[(8453, "0x2222222222222222222222222222222222222222")]
    assert only["score"] == 33
    assert only["source"] == "vault_list"


def test_two_vaults_same_protocol_different_scores(db: XerberusDatabase, monkeypatch: pytest.MonkeyPatch):
    fetched_at = datetime.datetime(2026, 7, 25, 12, 0, 0)
    db.insert_registry_snapshot_batch(
        [
            {
                "type": "pool",
                "id": "pool_a",
                "name": "Vault A",
                "chain": "ethereum",
                "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "chainId": 1,
                "score": 40,
            },
            {
                "type": "pool",
                "id": "pool_b",
                "name": "Vault B",
                "chain": "ethereum",
                "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "chainId": 1,
                "score": 90,
            },
            {
                "type": "protocol",
                "id": "proto_morpho",
                "name": "Morpho",
                "score": 84,
            },
        ],
        fetched_at,
    )

    from eth_defi.xerberus import mappings as mappings_mod

    monkeypatch.setitem(mappings_mod.XERBERUS_PROTOCOL_MAPPINGS, "morpho", "proto_morpho")

    pools = build_xerberus_pool_lookup(db, max_age_days=None)
    protocols = build_xerberus_protocols_for_export(db, ["morpho"], max_age_days=None)
    assert "morpho" in protocols
    assert protocols["morpho"]["score_scale"] == XERBERUS_SCORE_SCALE

    a = resolve_xerberus_vault_section(
        1,
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "morpho",
        pools,
        protocols,
    )
    b = resolve_xerberus_vault_section(
        1,
        "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "morpho",
        pools,
        protocols,
    )
    assert a is not None and b is not None
    assert a["score"] == 40
    assert b["score"] == 90
    assert a["entity_type"] == "pool"
    assert a["score_scale"] == XERBERUS_SCORE_SCALE


def test_protocol_fallback_when_pool_missing(db: XerberusDatabase, monkeypatch: pytest.MonkeyPatch):
    fetched_at = datetime.datetime(2026, 7, 25, 12, 0, 0)
    db.insert_registry_snapshot_batch(
        [{"type": "protocol", "id": "proto_x", "name": "X Proto", "score": 77}],
        fetched_at,
    )
    from eth_defi.xerberus import mappings as mappings_mod

    monkeypatch.setitem(mappings_mod.XERBERUS_PROTOCOL_MAPPINGS, "yearn", "proto_x")

    pools = build_xerberus_pool_lookup(db, max_age_days=None)
    protocols = build_xerberus_protocols_for_export(db, ["yearn"], max_age_days=None)
    section = resolve_xerberus_vault_section(
        1,
        "0xcccccccccccccccccccccccccccccccccccccccc",
        "yearn",
        pools,
        protocols,
    )
    assert section is not None
    assert section["entity_type"] == "protocol"
    assert section["score"] == 77
    assert section["protocol_slug"] == "yearn"


def test_report_urls_keyed_by_chain_and_address(db: XerberusDatabase):
    fetched_at = datetime.datetime(2026, 7, 25, 12, 0, 0)
    db.upsert_report_url(1, "0xAbC", "https://app.xerberus.io/pool/dendrogram/pool_1", fetched_at, entity_id="pool_1")
    db.upsert_report_url(42161, "0xAbC", "https://app.xerberus.io/pool/dendrogram/pool_2", fetched_at, entity_id="pool_2")
    assert db.get_report_url(1, "0xabc") == "https://app.xerberus.io/pool/dendrogram/pool_1"
    assert db.get_report_url(42161, "0xABC") == "https://app.xerberus.io/pool/dendrogram/pool_2"

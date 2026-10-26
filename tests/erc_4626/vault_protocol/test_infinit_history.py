# Fixtures use exact source values, naive UTC and private regression seams.
# ruff: noqa: PLR2004, DTZ001, PLC2701, ARG001
"""Offline regression coverage for Infinit source evidence and scoped repairs."""

import datetime
import json
import os
import zlib
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pyarrow as pa
import pytest
import requests
from eth_abi import encode

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.infinit import api, historical_context
from eth_defi.erc_4626.vault_protocol.infinit.api import InfinitPricePoint, parse_history_response
from eth_defi.erc_4626.vault_protocol.infinit.cleaning import apply_period_gap_exclusions, mask_infinit_gap_returns, mask_infinit_source_gaps
from eth_defi.erc_4626.vault_protocol.infinit.constants import APEX_PRODUCT, INFINIT_MANAGER, INFINIT_PRICE_GAPS, INFINIT_USDC
from eth_defi.erc_4626.vault_protocol.infinit.events import CREATED_TOPIC, DEPOSITED_TOPIC, TRANSFER_TOPIC, WITHDRAWN_TOPIC, ZERO_TOPIC, InfinitEvent, decode_infinit_event
from eth_defi.erc_4626.vault_protocol.infinit.historical_context import InfinitHistoricalContextStore, map_source_blocks
from eth_defi.erc_4626.vault_protocol.infinit.migration import InfinitMigrationConfig, run_staged
from eth_defi.erc_4626.vault_protocol.infinit.tags import get_strategy_tags
from eth_defi.erc_4626.vault_protocol.infinit.vault import InfinitVault
from eth_defi.research.vault_metrics import PeriodMetrics, calculate_hourly_returns_for_all_vaults
from eth_defi.research.wrangle_vault_prices import calculate_vault_returns, forward_fill_vault
from eth_defi.token import TokenDetails
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.risk import VAULT_PROTOCOL_RISK_MATRIX, VaultTechnicalRisk


def flight(data: list[dict], vault_id: str = APEX_PRODUCT.application_id) -> str:
    """Encode a small source response with its reviewed product identity."""
    return '0:{"a":"$@1"}\n1:' + json.dumps({"vaultId": vault_id, "data": data})


def test_infinit_classification_and_tags() -> None:
    """The receipt token and other chains must not inherit Apex's identity."""
    assert _get_hardcoded_protocol_features(APEX_PRODUCT.address, 42161) == {ERC4626Feature.infinit_like}
    assert _get_hardcoded_protocol_features(APEX_PRODUCT.address, 1) is None
    assert _get_hardcoded_protocol_features(APEX_PRODUCT.share_token, 42161) is None
    assert VAULT_PROTOCOL_RISK_MATRIX["Infinit"] == VaultTechnicalRisk.dangerous
    assert {t.value for t in get_strategy_tags(APEX_PRODUCT.address)} == {"algorithmic_trading", "delta_neutral", "perpetual_futures"}
    assert get_strategy_tags(APEX_PRODUCT.share_token) is None


def test_infinit_parse_duplicates_and_gaps() -> None:
    """Identical duplicates are harmless and raw corrupt samples remain evidence."""
    point = {"timestamp": "2026-07-30T10:00:00Z", "unitPrice": 0}
    history = parse_history_response(flight([point, point]))
    assert len(history.points) == 1
    assert history.points[0].price == Decimal(0)
    assert '"unitPrice": 0' in history.raw_response


@pytest.mark.parametrize("data", [[], [{"timestamp": "2026-07-16T10:00:00", "unitPrice": 1}], [{"timestamp": "2026-07-16T10:00:00Z", "unitPrice": -1}], [{"timestamp": "2026-07-16T10:00:00Z", "unitPrice": "NaN"}], [{"timestamp": "2026-07-16T10:00:00Z", "unitPrice": 1}, {"timestamp": "2026-07-16T10:00:00Z", "unitPrice": 2}]])
def test_infinit_reject_malformed_prices(data: list[dict]) -> None:
    """Malformed or conflicting source values never become a successful history."""
    with pytest.raises(ValueError):
        parse_history_response(flight(data))


def test_infinit_reject_truncated_and_wrong_identity() -> None:
    """Partial Flight payloads and another product must fail before writes."""
    with pytest.raises(ValueError):
        parse_history_response('1:{"vaultId":')
    with pytest.raises(ValueError):
        parse_history_response(flight([{"timestamp": "2026-07-16T10:00:00Z", "unitPrice": 1}], "another-vault"))


def test_infinit_action_build_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new public build resolves a new read-action identity automatically."""
    calls = []

    def fetch(session: requests.Session, method: str, url: str, **kwargs: object) -> str:
        calls.append(url)
        action = "abc123" if "old" in url else "def456"
        return 'createServerReference)("bad000",x,"getVaultDetail"),' + f'createServerReference)("{action}",x,"getVaultPerformanceHistory")'

    monkeypatch.setattr(api, "_fetch_text", fetch)
    api._fetch_action_for_build.cache_clear()
    assert api._fetch_action_for_build(("https://example.invalid/old.js",), "getVaultPerformanceHistory") == "abc123"
    assert api._fetch_action_for_build(("https://example.invalid/new.js",), "getVaultPerformanceHistory") == "def456"
    assert len(calls) == 2
    api._fetch_action_for_build.cache_clear()


def test_infinit_event_routing() -> None:
    """Manager vault IDs resolve to the actual vault rather than the emitter."""
    log = SimpleNamespace(address=INFINIT_MANAGER, topics=[CREATED_TOPIC, hex(1), "0x" + APEX_PRODUCT.address[2:].rjust(64, "0")], data="0x" + encode(["address", "address"], [APEX_PRODUCT.share_token, INFINIT_USDC]).hex(), block_number=APEX_PRODUCT.deployment_block, log_index=2, transaction_hash="0x1234")
    assert decode_infinit_event(log, APEX_PRODUCT).kind == "created"
    log.topics = [DEPOSITED_TOPIC, "0x" + "1" * 64, hex(1)]
    log.data = "0x" + encode(["uint256"], [1_000_000]).hex()
    assert decode_infinit_event(log, APEX_PRODUCT).amount == 1_000_000
    log.topics[-1] = hex(2)
    assert decode_infinit_event(log, APEX_PRODUCT) is None
    log.topics = [WITHDRAWN_TOPIC, "0x" + "1" * 64, hex(1)]
    log.data = "0x" + encode(["uint256", "uint256"], [1_000_000, 1_100_000]).hex()
    withdrawal = decode_infinit_event(log, APEX_PRODUCT)
    assert withdrawal.kind == "withdraw"
    assert withdrawal.amount == 1_000_000
    log.address = APEX_PRODUCT.share_token
    log.topics = [TRANSFER_TOPIC, "0x" + "1" * 64, ZERO_TOPIC]
    log.data = "0x" + encode(["uint256"], [1_000_000]).hex()
    burn = decode_infinit_event(log, APEX_PRODUCT)
    assert burn.kind == "burn"
    assert burn.amount == withdrawal.amount


@pytest.mark.parametrize("age,available", [(14, True), (20, True), (30, True), (31, False)])
def test_infinit_price_freshness(age: int, *, available: bool) -> None:
    """Current valuation tolerates bounded publication lag without fabricating history."""
    point = InfinitPricePoint(datetime.datetime(2026, 7, 16, 10), Decimal("1.01"))
    vault = object.__new__(InfinitVault)
    stamp = point.timestamp + datetime.timedelta(minutes=age)
    vault.web3 = SimpleNamespace(eth=SimpleNamespace(get_block=lambda _block: {"timestamp": stamp.replace(tzinfo=datetime.UTC).timestamp()}))
    vault.source_history = api.InfinitHistory(APEX_PRODUCT.application_id, (point,), "fixture", "fixture")
    assert vault.fetch_share_price() == (point.price if available else None)
    stamp = INFINIT_PRICE_GAPS[0][0]
    assert vault.fetch_share_price() is None


def test_infinit_store_retention_and_supply(tmp_path: Path) -> None:
    """Repeated and shortened responses retain old observations without duplicates."""
    t = datetime.datetime(2026, 7, 16, 10)
    history = parse_history_response(flight([{"timestamp": "2026-07-16T10:00:00Z", "unitPrice": 1}, {"timestamp": "2026-07-16T11:00:00Z", "unitPrice": 1.01}]))
    blocks = {t: 10, t + datetime.timedelta(hours=1): 20}
    events = [InfinitEvent(1, 0, 0, "created", 0, "0x1"), InfinitEvent(2, 0, 0, "mint", 100, "0x2"), InfinitEvent(15, 0, 0, "burn", 10, "0x3")]
    with InfinitHistoricalContextStore(tmp_path / "history.duckdb") as store:
        for _ in range(2):
            store.store(APEX_PRODUCT, history, history.points, blocks, events, 1, 30, expected_supply=90)
        assert [r[3] for r in store.iter_prices(APEX_PRODUCT, 1, 30)] == [100, 90]
        shorter = parse_history_response(flight([{"timestamp": "2026-07-16T11:00:00Z", "unitPrice": 1.01}]))
        store.store(APEX_PRODUCT, shorter, shorter.points, blocks, events, 1, 30, expected_supply=90)
        assert len(tuple(store.iter_prices(APEX_PRODUCT, 1, 30))) == 2
        assert store.connection.execute("SELECT count(*) FROM infinit_sources").fetchone()[0] == 2
        payload = store.connection.execute("SELECT payload FROM infinit_sources ORDER BY fetched_at LIMIT 1").fetchone()[0]
        assert zlib.decompress(payload).decode() == history.raw_response
        with pytest.raises(ValueError, match="Incomplete Infinit supply"):
            store.store(APEX_PRODUCT, history, history.points, blocks, [], 1, 30, expected_supply=90)
        assert store.activity_counts(APEX_PRODUCT)["mint"] == 1
        assert not store.connection.execute("SELECT * FROM duckdb_constraints()").fetchall()


def test_infinit_hourly_reader_preserves_source_offsets_and_gaps(tmp_path: Path) -> None:
    """Observed hourly samples retain timestamp offsets, gaps and block-matched supply."""
    history = parse_history_response(
        flight(
            [
                {"timestamp": "2026-07-30T10:00:01Z", "unitPrice": 0},
                {"timestamp": "2026-08-04T02:00:01Z", "unitPrice": 1.05},
                {"timestamp": "2026-08-04T02:15:01Z", "unitPrice": 1.06},
            ]
        )
    )
    blocks = dict(zip((point.timestamp for point in history.points), (10, 20, 30), strict=True))
    events = [InfinitEvent(1, 0, 0, "created", 0, "0x1"), InfinitEvent(2, 0, 0, "mint", 1000000, "0x2"), InfinitEvent(15, 0, 0, "burn", 500000, "0x3")]
    vault = object.__new__(InfinitVault)
    vault.product = APEX_PRODUCT
    vault.historical_context_path = tmp_path / "context.duckdb"
    vault.share_token = TokenDetails(contract=None, decimals=6)
    with InfinitHistoricalContextStore(vault.historical_context_path) as store:
        store.store(APEX_PRODUCT, history, history.points, blocks, events, 1, 31, expected_supply=500000)
    reads = tuple(vault.get_historical_reader(stateful=False).fetch_contextual_historical_reads(1, 31, 1))
    assert len(reads) == 2
    assert reads[0].share_price is None
    assert reads[0].total_assets is None
    assert reads[0].total_supply == Decimal(1)
    assert reads[1].timestamp == history.points[1].timestamp
    assert reads[1].share_price == Decimal("1.05")
    assert reads[1].total_supply == Decimal("0.5")
    assert reads[1].total_assets == Decimal("0.525")


def test_infinit_dense_mapping(tmp_path: Path) -> None:
    """Exact timestamps are read without filling missing cache blocks."""
    con = duckdb.connect(str(tmp_path / "42161-timestamps.duckdb"))
    try:
        con.execute("CREATE TABLE block_timestamps AS SELECT * FROM (VALUES (10,1784196000),(11,1784196000),(12,1784196001)) t(block_number,timestamp)")
    finally:
        con.close()
    point = InfinitPricePoint(datetime.datetime(2026, 7, 16, 10), Decimal(1))
    assert map_source_blocks((point,), tmp_path, 10, 13)[point.timestamp] == 11
    with pytest.raises(ValueError, match="dense"):
        map_source_blocks((point,), tmp_path, 10, 14)


def test_infinit_recurring_timestamp_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recurring scans extend a warm cache but refuse missing historical coverage."""
    product = replace(APEX_PRODUCT, deployment_block=10)
    calls = []

    def fetch(**kwargs: object) -> SimpleNamespace:
        """Record the exact inclusive timestamp range without accessing a provider."""
        calls.append(kwargs)
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(historical_context, "fetch_block_timestamps_using_hypersync_cached", fetch)
    with pytest.raises(ValueError, match="prepopulate"):
        historical_context.fetch_infinit_timestamp_tail(object(), product, 14, tmp_path)
    con = duckdb.connect(str(tmp_path / "42161-timestamps.duckdb"))
    try:
        con.execute("CREATE TABLE block_timestamps AS SELECT i AS block_number, 1784196000 AS timestamp FROM range(10,13) t(i)")
    finally:
        con.close()
    historical_context.fetch_infinit_timestamp_tail(object(), product, 13, tmp_path)
    assert not calls
    historical_context.fetch_infinit_timestamp_tail(object(), product, 15, tmp_path)
    assert calls[0]["start_block"] == 13
    assert calls[0]["end_block"] == 14
    con = duckdb.connect(str(tmp_path / "42161-timestamps.duckdb"))
    try:
        con.execute("DELETE FROM block_timestamps WHERE block_number=11")
    finally:
        con.close()
    with pytest.raises(ValueError, match="dense"):
        historical_context.fetch_infinit_timestamp_tail(object(), product, 15, tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("start,end", INFINIT_PRICE_GAPS)
def test_infinit_gap_cleaning_and_returns(start: datetime.datetime, end: datetime.datetime) -> None:
    """Both inclusive gaps stay missing and no return bridges either boundary."""
    timestamps = [start - datetime.timedelta(hours=1), start, end, end + datetime.timedelta(hours=1), end + datetime.timedelta(hours=2)]
    ids = [f"42161-{APEX_PRODUCT.address}", "42161-0xunrelated"]
    frame = pd.DataFrame({"id": [i for i in ids for _ in timestamps], "timestamp": timestamps * 2, "share_price": [1.0, 1.0, 1.0, 1.05, 1.06] * 2, "returns_1h": [0.0] * 10})
    mask_infinit_source_gaps(frame)
    assert frame.share_price.isna().sum() == 2
    frame = frame.dropna(subset=["share_price"])
    frame["returns_1h"] = frame.groupby("id").share_price.pct_change()
    mask_infinit_gap_returns(frame)
    assert pd.isna(frame.loc[3, "returns_1h"])
    assert frame.loc[4, "returns_1h"] == pytest.approx(1.06 / 1.05 - 1)
    assert frame.loc[8, "returns_1h"] == pytest.approx(0.05)


def test_infinit_staged_dry_run_apply_and_failure(tmp_path: Path) -> None:
    """Real staged changes publish only on apply and repeat without state loss."""
    target = tmp_path / "metadata.bin"
    target.write_bytes(b"original")
    reader = tmp_path / "vault-reader-state-1h.pickle"
    reader.write_bytes(b"critical reader state")
    config = InfinitMigrationConfig(tmp_path, target, tmp_path / "raw", tmp_path / "cleaned", tmp_path / "context", tmp_path)

    def update(staged: dict[Path, Path]) -> dict[str, int]:
        assert staged[target].read_bytes() in {b"original", b"updated"}
        staged[target].write_bytes(b"updated")
        return {"rows": 1}

    assert run_staged(config, (target,), update)["mode"] == "dry-run"
    assert target.read_bytes() == b"original"
    config.dry_run = False
    for _ in range(2):
        assert run_staged(config, (target,), update)["mode"] == "applied"
    assert target.read_bytes() == b"updated"

    assert len(tuple(tmp_path.glob("*.bak"))) == 2
    assert reader.read_bytes() == b"critical reader state"

    def fail(staged: dict[Path, Path]) -> None:
        staged[target].write_bytes(b"partial")
        msg = "source failed"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="source failed"):
        run_staged(config, (target,), fail)
    assert target.read_bytes() == b"updated"


def test_infinit_common_returns_preserve_gap() -> None:
    """The actual common return calculator cannot produce a cross-gap return."""
    start, end = INFINIT_PRICE_GAPS[0]
    frame = pd.DataFrame({"id": [f"42161-{APEX_PRODUCT.address}"] * 4, "timestamp": [start - datetime.timedelta(hours=1), start, end + datetime.timedelta(minutes=15), end + datetime.timedelta(hours=1, minutes=15)], "share_price": [1.0, 0.0, 1.04, 1.05]})
    result = calculate_vault_returns(frame, logger=lambda _message: None)
    assert len(result) == 3
    assert pd.isna(result.iloc[1].returns_1h)
    assert result.iloc[2].returns_1h == pytest.approx(1.05 / 1.04 - 1)


def test_infinit_file_backed_bulk_deduplication(tmp_path: Path) -> None:
    """Exercise transactional joins over a million unconstrained persistent rows."""
    path = tmp_path / "bulk.duckdb"
    stamp = datetime.datetime(2026, 7, 16, 10)
    history = parse_history_response(flight([{"timestamp": "2026-07-16T10:00:00Z", "unitPrice": 1}]))
    with InfinitHistoricalContextStore(path) as store:
        store.connection.execute("INSERT INTO infinit_prices SELECT ?, TIMESTAMP '2026-01-01' + i * INTERVAL 1 SECOND, '1', i FROM range(1000000) t(i)", [APEX_PRODUCT.address])
        for _ in range(2):
            store.store(APEX_PRODUCT, history, history.points, {stamp: 1000001}, [], 1, 2)
        assert store.connection.execute("SELECT count(*) FROM infinit_prices").fetchone()[0] == 1000001
        assert store.connection.execute("SELECT count(*) FROM infinit_prices WHERE timestamp=?", [stamp]).fetchone()[0] == 1
    with InfinitHistoricalContextStore(path, read_only=True) as store:
        assert store.connection.execute("SELECT count(*) FROM infinit_sources").fetchone()[0] == 1


def test_infinit_defaults_and_unreadable_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment defaults are dry-run and corrupt existing history is fatal."""
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DRY_RUN", raising=False)
    config = InfinitMigrationConfig.from_env()
    assert config.dry_run
    assert config.metadata == tmp_path / "vault-metadata-db.pickle"
    config.raw.write_bytes(b"unreadable parquet must survive")
    with pytest.raises(pa.ArrowInvalid, match="Parquet"):
        run_staged(config, (config.raw,), lambda _staged: {})
    assert config.raw.read_bytes() == b"unreadable parquet must survive"


def test_infinit_uncheckpointed_stage_is_not_published(tmp_path: Path) -> None:
    """A leftover staged WAL must not be separated from its committed database."""
    target = tmp_path / "context.duckdb"
    target.write_bytes(b"original")
    config = InfinitMigrationConfig(tmp_path, tmp_path / "metadata", tmp_path / "raw", tmp_path / "cleaned", target, tmp_path, dry_run=False)

    def update(staged: dict[Path, Path]) -> dict[str, int]:
        """Simulate a callback returning before its staged database checkpoint."""
        staged[target].write_bytes(b"updated")
        Path(str(staged[target]) + ".wal").write_bytes(b"uncheckpointed writes")
        return {"rows": 1}

    with pytest.raises(ValueError, match="uncheckpointed WAL"):
        run_staged(config, (target,), update)
    assert target.read_bytes() == b"original"


def test_infinit_display_daily_and_period_gap_preservation() -> None:
    """Display filling, daily aggregation and public lookbacks respect exclusions."""
    start, end = INFINIT_PRICE_GAPS[0]
    frame = pd.DataFrame({"chain": [42161] * 3, "address": [APEX_PRODUCT.address] * 3, "id": [f"42161-{APEX_PRODUCT.address}"] * 3, "share_price": [1.0, 1.04, 1.05]}, index=pd.DatetimeIndex([start - datetime.timedelta(hours=1), end + datetime.timedelta(hours=1), end + datetime.timedelta(days=2)]))
    filled = forward_fill_vault(frame)
    assert filled.loc[start:end, "share_price"].isna().all()
    daily = calculate_hourly_returns_for_all_vaults(frame)
    assert daily.loc[start:end, "share_price"].isna().all()
    assert pd.isna(daily.loc[pd.Timestamp("2026-08-05"), "returns_1h"])
    period = PeriodMetrics("lifetime", samples_start_at=pd.Timestamp("2026-07-16"), samples_end_at=pd.Timestamp("2026-09-16"), returns_gross=0.05, cagr_gross=0.2)
    excluded = next(apply_period_gap_exclusions(VaultSpec(42161, APEX_PRODUCT.address), [period]))
    assert excluded.returns_gross is None
    assert excluded.cagr_gross is None
    assert "gap" in excluded.error_reason
    assert next(apply_period_gap_exclusions(VaultSpec(1, APEX_PRODUCT.address), [period])) is period


def test_infinit_partial_publication_restores_backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An I/O failure after the first replacement restores original datasets."""
    first, second = tmp_path / "first.bin", tmp_path / "second.bin"
    first.write_bytes(b"first original")
    second.write_bytes(b"second original")
    config = InfinitMigrationConfig(tmp_path, first, second, tmp_path / "cleaned", tmp_path / "context", tmp_path, dry_run=False)
    original_replace = os.replace
    calls = 0

    def replace(source: Path | str, destination: Path | str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            message = "Simulated publication failure"
            raise OSError(message)
        original_replace(source, destination)

    def update(staged: dict[Path, Path]) -> dict[str, int]:
        for stage in staged.values():
            stage.write_bytes(b"replacement")
        return {"files": 2}

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(OSError, match="publication failure"):
        run_staged(config, (first, second), update)
    assert first.read_bytes() == b"first original"
    assert second.read_bytes() == b"second original"
    assert len(tuple(tmp_path.glob("*.bak"))) == 2

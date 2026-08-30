"""Tests for the GMX historical-observation DuckDB table."""

import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from eth_utils import to_checksum_address

from eth_defi.gmx import historical_context
from eth_defi.gmx.historical_context import GMXHistoricalContextStore, fetch_and_store_gmx_historical_share_prices
from eth_defi.gmx.historical_oracle import GMXHistoricalSharePriceObservation

TOKEN = to_checksum_address("0x1000000000000000000000000000000000000001")


def _observation(block_number: int, log_index: int = 1, raw_value: int = 10**30) -> GMXHistoricalSharePriceObservation:
    """Create one deterministic GMX value event."""

    return GMXHistoricalSharePriceObservation(
        chain_id=42161,
        block_number=block_number,
        block_timestamp=1_700_000_000 + block_number,
        transaction_hash=f"0x{block_number:064x}",
        log_index=log_index,
        product_address=TOKEN,
        raw_value=raw_value,
        raw_supply=10**18,
        event_name="MarketPoolValueUpdated",
    )


def test_share_price_events_are_idempotent_and_downsampled(tmp_path: Path) -> None:
    """Retain the last GMX value event in each common scan bucket."""

    early = _observation(5)
    late = _observation(9, 2, 2 * 10**30)
    next_bucket = _observation(15, raw_value=3 * 10**30)
    with GMXHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
        assert store.insert_share_price(early)
        assert not store.insert_share_price(early)
        assert store.insert_share_price(late)
        assert store.insert_share_price(next_bucket)
        selected = tuple(store.iter_share_prices(chain_id=42161, product_address=TOKEN, start_block=0, end_block=20, step=10))

    assert selected == (late, next_bucket)
    assert selected[0].share_price == pytest.approx(2)


def test_legacy_context_table_is_migrated_to_direct_columns(tmp_path: Path) -> None:
    """Preserve valid observations while removing the generic JSON envelope."""

    path = tmp_path / "vault-historical-context.duckdb"
    observation = _observation(5)
    payload = json.dumps(
        {
            "block_timestamp": observation.block_timestamp,
            "event_name": observation.event_name,
            "log_index": observation.log_index,
            "product_address": observation.product_address.lower(),
            "raw_supply": str(observation.raw_supply),
            "raw_value": str(observation.raw_value),
            "transaction_hash": observation.transaction_hash.lower(),
        }
    )
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE gmx_historical_context (
                chain_id UINTEGER,
                sample_block_number UBIGINT,
                valuation_context VARCHAR,
                source_observation_id VARCHAR,
                token_coverage_hash VARCHAR,
                payload_hash VARCHAR,
                schema_version UINTEGER,
                context_json JSON
            )
            """
        )
        connection.execute(
            "INSERT INTO gmx_historical_context VALUES (?, ?, 'lp_share_price', 'source', 'coverage', 'payload', 3, ?)",
            (observation.chain_id, observation.block_number, payload),
        )
    finally:
        connection.close()

    with GMXHistoricalContextStore(path) as store:
        selected = tuple(store.iter_share_prices(chain_id=42161, product_address=TOKEN, start_block=0, end_block=10, step=10))
        columns = {row[0] for row in store.connection.execute("DESCRIBE gmx_historical_context").fetchall()}

    assert selected == (observation,)
    assert columns == historical_context.GMX_HISTORICAL_CONTEXT_COLUMNS


def test_primary_key_table_is_migrated_without_art_index(tmp_path: Path) -> None:
    """Preserve observations while removing the unsafe DuckDB ART index."""

    path = tmp_path / "vault-historical-context.duckdb"
    observation = _observation(5)
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE gmx_historical_context (
                chain_id UINTEGER NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                transaction_hash VARCHAR NOT NULL,
                log_index UINTEGER NOT NULL,
                product_address VARCHAR NOT NULL,
                raw_value UHUGEINT NOT NULL,
                raw_supply UHUGEINT NOT NULL,
                event_name VARCHAR NOT NULL,
                PRIMARY KEY (chain_id, transaction_hash, log_index)
            )
            """
        )
        connection.execute(
            "INSERT INTO gmx_historical_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            GMXHistoricalContextStore._observation_to_values(observation),
        )
    finally:
        connection.close()

    with GMXHistoricalContextStore(path) as store:
        assert not store._has_art_index("gmx_historical_context")
        selected = tuple(store.iter_share_prices(chain_id=42161, product_address=TOKEN, start_block=0, end_block=10, step=10))

    assert selected == (observation,)


def test_batch_insert_is_idempotent_without_constraints(tmp_path: Path) -> None:
    """Deduplicate a complete chunk through application-level hash joins."""

    observations = (_observation(5), _observation(6), _observation(6))
    with GMXHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
        assert store.insert_share_prices(observations) == 2
        assert store.insert_share_prices(observations) == 0
        assert not store._has_art_index("gmx_historical_context")


def test_batch_insert_rejects_conflict_within_batch(tmp_path: Path) -> None:
    """Reject two payloads carrying the same source-event identity."""

    observations = (_observation(5), _observation(5, raw_value=2 * 10**30))
    with GMXHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
        with pytest.raises(ValueError, match="conflict"):
            store.insert_share_prices(observations)


def test_share_price_reader_rejects_conflicting_source_event(tmp_path: Path) -> None:
    """Reject different values for the same chain transaction log."""

    with GMXHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
        assert store.insert_share_price(_observation(5))
        with pytest.raises(ValueError, match="conflict"):
            store.insert_share_price(_observation(5, raw_value=2 * 10**30))


def test_prefill_chunks_and_commits_completed_ranges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded, interrupted backfill retains observations from completed chunks."""

    context_path = tmp_path / "vault-historical-context.duckdb"
    ranges: list[tuple[int, int]] = []
    event_loop_runners: list[object] = []

    def fetch_observations(**kwargs: object) -> list[GMXHistoricalSharePriceObservation]:
        start_block = int(kwargs["start_block"])
        end_block = int(kwargs["end_block"])
        ranges.append((start_block, end_block))
        event_loop_runners.append(kwargs["event_loop_runner"])
        if start_block == 0:
            return [_observation(5)]
        message = "interrupted source"
        raise RuntimeError(message)

    monkeypatch.setattr(historical_context, "fetch_historical_share_price_observations_hypersync", fetch_observations)
    with pytest.raises(RuntimeError, match="interrupted source"):
        fetch_and_store_gmx_historical_share_prices(
            web3=SimpleNamespace(eth=SimpleNamespace(chain_id=42161)),
            hypersync_client=object(),
            start_block=0,
            end_block=25,
            context_path=context_path,
            source_chunk_size=10,
        )

    assert ranges == [(0, 10), (10, 20)]
    assert event_loop_runners[0] is event_loop_runners[1]
    with GMXHistoricalContextStore(context_path) as store:
        persisted = tuple(store.iter_share_prices(chain_id=42161, product_address=TOKEN, start_block=0, end_block=10, step=10))
    assert persisted == (_observation(5),)


def test_prefill_retries_http_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry the HTTP error form returned by explicit Hypersync pages."""

    attempts = 0
    delays: list[float] = []

    def fetch_observations(**_kwargs: object) -> list[GMXHistoricalSharePriceObservation]:
        """Fail once with the direct-request rate-limit wording."""

        nonlocal attempts
        attempts += 1
        if attempts == 1:
            message = "429 Too Many Requests"
            raise RuntimeError(message)
        return []

    monkeypatch.setattr(historical_context, "fetch_historical_share_price_observations_hypersync", fetch_observations)
    monkeypatch.setattr(historical_context.time, "sleep", delays.append)

    fetch_and_store_gmx_historical_share_prices(
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=42161)),
        hypersync_client=object(),
        start_block=0,
        end_block=10,
        context_path=tmp_path / "vault-historical-context.duckdb",
    )

    assert attempts == 2
    assert delays == [35]

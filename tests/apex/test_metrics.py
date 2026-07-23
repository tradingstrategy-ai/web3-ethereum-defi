"""ApeX DuckDB lifecycle and append-and-correct tests."""

# ruff: noqa: ARG005, DTZ001, PLR2004

import datetime
import threading
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from pytest import MonkeyPatch

from eth_defi.apex.metrics import ApexMetricsDatabase, run_scan
from eth_defi.apex.session import ApexAPIError
from eth_defi.apex.vault import ApexHistoryPoint, ApexVaultSummary


def _vault(
    vault_id: str,
    *,
    status: str = "VAULT_IN_PROCESS",
    nav: float = 1.0,
    address: str = "0xdb246af9ef918be85ea7cf98925480ff367a7038",
) -> ApexVaultSummary:
    now = datetime.datetime(2026, 7, 23, 12)
    return ApexVaultSummary(
        vault_id=vault_id,
        synthetic_address=f"apex-vault-{vault_id}",
        reported_ethereum_address=address,
        name=f"Vault {vault_id}",
        description="Fixture",
        status=status,
        vault_type="NOT_COLLECT_VAULT",
        share_price=nav,
        tvl=nav * 100,
        share_count=100,
        created_at=now - datetime.timedelta(days=1),
        source_updated_at=now,
        finished_at=now if status == "VAULT_FINISHED" else None,
        max_amount=1000,
        purchase_fee_rate_raw="0",
        share_profit_ratio_raw="",
    )


@pytest.fixture
def database(tmp_path: Path) -> Iterator[ApexMetricsDatabase]:
    """Yield an owner-thread file-backed ApeX database."""
    db = ApexMetricsDatabase(tmp_path / "apex.duckdb")
    try:
        yield db
    finally:
        if db.con is not None:
            db.close()


def test_schema_has_no_art_constraints(database: ApexMetricsDatabase) -> None:
    """Keep the file-backed schema free of ART-backed logical constraints."""
    constraints = database.con.execute(
        """
        SELECT constraint_type
        FROM duckdb_constraints()
        WHERE table_name IN ('vault_metadata', 'vault_prices', 'history_sync')
        """
    ).fetchall()
    assert not any(row[0] in {"PRIMARY KEY", "UNIQUE"} for row in constraints)
    value = database.con.execute("SELECT current_setting('wal_autocheckpoint')").fetchone()[0]
    assert value in {"1.0 TiB", "931.3 GiB"}


def test_shared_ethereum_address_does_not_merge_vaults(database: ApexMetricsDatabase) -> None:
    """Key vaults by platform ID instead of their shared Ethereum metadata."""
    observed_at = datetime.datetime(2026, 7, 23, 12)
    database.apply_ranking((_vault("1"), _vault("2")), observed_at, manage_disappearance=True)
    metadata = database.get_vault_metadata()
    assert len(metadata) == 2
    assert metadata["reported_ethereum_address"].nunique() == 1
    assert metadata["synthetic_address"].nunique() == 2
    assert len(database.get_vault_prices()) == 2


def test_history_is_append_and_correct_and_precedes_ranking(database: ApexMetricsDatabase) -> None:
    """Preserve omitted history while correcting returned timestamps."""
    timestamp = datetime.datetime(2026, 7, 23, 12)
    database.apply_ranking((_vault("1"),), timestamp, manage_disappearance=True)
    database.apply_history_success(
        "1",
        (
            ApexHistoryPoint(timestamp, 1.25, 125),
            ApexHistoryPoint(timestamp + datetime.timedelta(hours=1), 1.5, 150),
        ),
        timestamp + datetime.timedelta(minutes=1),
    )
    prices = database.get_vault_prices("1")
    assert len(prices) == 2
    assert set(prices["source"]) == {"fund_net_values"}
    assert prices.iloc[0]["share_price"] == pytest.approx(1.25)
    assert pd.isna(prices.iloc[0]["source_updated_at"])

    database.apply_history_success(
        "1",
        (ApexHistoryPoint(timestamp, 1.30, 130),),
        timestamp + datetime.timedelta(hours=2),
    )
    prices = database.get_vault_prices("1")
    assert len(prices) == 2
    assert prices.iloc[0]["share_price"] == pytest.approx(1.30)

    database.apply_history_success("1", (), timestamp + datetime.timedelta(hours=3))
    assert len(database.get_vault_prices("1")) == 2
    sync = database.get_history_sync().iloc[0]
    assert sync["latest_attempt_row_count"] == 0
    assert sync["latest_nonempty_row_count"] == 1
    assert sync["canonical_history_row_count"] == 2


def test_terminal_reactivation_starts_new_generation(database: ApexMetricsDatabase) -> None:
    """Clear and restart terminal history generations on reactivation."""
    first = datetime.datetime(2026, 7, 23, 12)
    database.apply_ranking((_vault("1", status="VAULT_FINISHED"),), first, manage_disappearance=True)
    database.apply_history_success("1", (ApexHistoryPoint(first, 1, 100),), first + datetime.timedelta(minutes=1))
    sync = database.get_history_sync().iloc[0]
    assert sync["terminal_observed_at"] == first
    assert sync["final_history_sync_at"] == first + datetime.timedelta(minutes=1)
    database.apply_history_success("1", (ApexHistoryPoint(first, 1.1, 110),), first + datetime.timedelta(minutes=2))
    sync = database.get_history_sync().iloc[0]
    assert sync["final_history_sync_at"] == first + datetime.timedelta(minutes=1)

    second = first + datetime.timedelta(hours=1)
    database.apply_ranking((_vault("1"),), second, manage_disappearance=True)
    sync = database.get_history_sync().iloc[0]
    assert pd.isna(sync["terminal_observed_at"])
    assert pd.isna(sync["final_history_sync_at"])

    third = second + datetime.timedelta(hours=1)
    database.apply_ranking((_vault("1", status="VAULT_FINISHED"),), third, manage_disappearance=True)
    sync = database.get_history_sync().iloc[0]
    assert sync["terminal_observed_at"] == third
    assert pd.isna(sync["final_history_sync_at"])


def test_disappearance_and_targeted_reappearance(database: ApexMetricsDatabase) -> None:
    """Clear only the selected missing generation on targeted reappearance."""
    first = datetime.datetime(2026, 7, 23, 12)
    database.apply_ranking((_vault("1"), _vault("2")), first, manage_disappearance=True)
    missing_at = first + datetime.timedelta(hours=1)
    database.apply_ranking((_vault("2"),), missing_at, manage_disappearance=True)
    metadata = database.get_vault_metadata().set_index("vault_id")
    assert metadata.loc["1", "missing_since"] == missing_at

    reappeared_at = missing_at + datetime.timedelta(hours=1)
    database.apply_ranking((_vault("1"),), reappeared_at, manage_disappearance=False)
    metadata = database.get_vault_metadata().set_index("vault_id")
    assert pd.isna(metadata.loc["1", "missing_since"])
    assert pd.isna(metadata.loc["2", "missing_since"])
    sync = database.get_history_sync().set_index("vault_id")
    assert pd.isna(sync.loc["1", "missing_observed_at"])


def test_wrong_thread_database_use_fails(database: ApexMetricsDatabase) -> None:
    """Reject checkpoints attempted outside the creating thread."""
    errors = []

    def worker() -> None:
        try:
            database.checkpoint()
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert len(errors) == 1


def test_file_backed_reopen_is_idempotent(tmp_path: Path) -> None:
    """Checkpoint, reopen and repeat many writes without logical duplicates."""
    path = tmp_path / "reopen.duckdb"
    observed_at = datetime.datetime(2026, 7, 23, 12)
    db = ApexMetricsDatabase(path)
    try:
        for hour in range(25):
            timestamp = observed_at + datetime.timedelta(hours=hour)
            vault = _vault("1", nav=1 + hour / 100)
            db.apply_ranking((vault,), timestamp, manage_disappearance=True)
            db.apply_ranking((vault,), timestamp, manage_disappearance=True)
        db.checkpoint()
        assert len(db.get_vault_prices("1")) == 25
    finally:
        db.close()
    reopened = ApexMetricsDatabase(path)
    try:
        assert len(reopened.get_vault_metadata()) == 1
        assert len(reopened.get_vault_prices("1")) == 25
    finally:
        reopened.close()


def test_interrupted_history_transaction_rolls_back_and_recovers(database: ApexMetricsDatabase, monkeypatch: MonkeyPatch) -> None:
    """Roll back an arbitrary interruption and keep the connection usable."""

    class InjectedInterruption(BaseException):
        """Synthetic process interruption raised after history replacement."""

    observed_at = datetime.datetime(2026, 7, 23, 12)
    database.apply_ranking((_vault("1"),), observed_at, manage_disappearance=True)

    def interrupt(_rows: object) -> None:
        raise InjectedInterruption

    with monkeypatch.context() as patch:
        patch.setattr(database, "_replace_sync", interrupt)
        with pytest.raises(InjectedInterruption):
            database.apply_history_success(
                "1",
                (ApexHistoryPoint(observed_at, 1.25, 125),),
                observed_at + datetime.timedelta(minutes=1),
            )

    prices = database.get_vault_prices("1")
    assert len(prices) == 1
    assert prices.iloc[0]["source"] == "ranking_snapshot"
    database.apply_history_success(
        "1",
        (ApexHistoryPoint(observed_at, 1.25, 125),),
        observed_at + datetime.timedelta(minutes=2),
    )
    assert database.get_vault_prices("1").iloc[0]["source"] == "fund_net_values"


def test_history_gate_handles_empty_terminal_refresh_and_missing(database: ApexMetricsDatabase) -> None:
    """Gate incremental, terminal, forced and missing history generations."""
    first = datetime.datetime(2026, 7, 23, 12)
    interval = datetime.timedelta(hours=24)
    database.apply_ranking((_vault("1"), _vault("2")), first, manage_disappearance=True)
    assert database.select_history_candidates({"1", "2"}, first, mode="incremental", refresh_interval=interval, include_missing=True) == ("1", "2")

    database.apply_history_success("1", (), first + datetime.timedelta(minutes=1))
    assert database.select_history_candidates({"1", "2"}, first + datetime.timedelta(hours=1), mode="incremental", refresh_interval=interval, include_missing=True) == ("2",)
    assert database.select_history_candidates({"1", "2"}, first + datetime.timedelta(hours=25), mode="incremental", refresh_interval=interval, include_missing=True) == ("1", "2")

    terminal_at = first + datetime.timedelta(hours=26)
    database.apply_ranking((_vault("1", status="VAULT_FINISHED"), _vault("2")), terminal_at, manage_disappearance=True)
    database.apply_history_success("1", (), terminal_at + datetime.timedelta(minutes=1))
    assert database.select_history_candidates({"1", "2"}, terminal_at + datetime.timedelta(minutes=2), mode="incremental", refresh_interval=interval, include_missing=True) == ("1", "2")
    database.apply_history_success("1", (ApexHistoryPoint(terminal_at, 1, 100),), terminal_at + datetime.timedelta(minutes=3))
    final_sync_at = database.get_history_sync().set_index("vault_id").loc["1", "final_history_sync_at"]
    assert database.select_history_candidates({"1", "2"}, terminal_at + datetime.timedelta(minutes=4), mode="incremental", refresh_interval=interval, include_missing=True) == ("2",)
    assert database.select_history_candidates({"1", "2"}, terminal_at + datetime.timedelta(minutes=4), mode="refresh", refresh_interval=interval, include_missing=True) == ("1", "2")
    database.apply_history_success("1", (ApexHistoryPoint(terminal_at, 1.1, 110),), terminal_at + datetime.timedelta(minutes=5))
    assert database.get_history_sync().set_index("vault_id").loc["1", "final_history_sync_at"] == final_sync_at

    missing_at = terminal_at + datetime.timedelta(hours=1)
    database.apply_ranking((_vault("1", status="VAULT_FINISHED"),), missing_at, manage_disappearance=True)
    assert "2" in database.select_history_candidates({"1"}, missing_at, mode="incremental", refresh_interval=interval, include_missing=True)
    database.apply_history_success("2", (ApexHistoryPoint(missing_at, 1, 100),), missing_at + datetime.timedelta(minutes=1))
    assert "2" not in database.select_history_candidates({"1"}, missing_at + datetime.timedelta(minutes=2), mode="incremental", refresh_interval=interval, include_missing=True)


def test_unchanged_terminal_ranking_is_suppressed(database: ApexMetricsDatabase) -> None:
    """Avoid repeated ranking rows for an unchanged terminal vault."""
    first = datetime.datetime(2026, 7, 23, 12)
    database.apply_ranking((_vault("1", status="VAULT_FINISHED"),), first, manage_disappearance=True)
    database.apply_ranking((_vault("1", status="VAULT_FINISHED"),), first + datetime.timedelta(hours=1), manage_disappearance=True)
    assert len(database.get_vault_prices("1")) == 1


def test_run_scan_validates_target_before_mutation(database: ApexMetricsDatabase, monkeypatch: MonkeyPatch) -> None:
    """Reject missing target IDs before any database mutation."""
    monkeypatch.setattr("eth_defi.apex.metrics.fetch_stabilised_vaults", lambda *args, **kwargs: (_vault("1"),))
    with pytest.raises(ValueError, match="absent"):
        run_scan(object(), database, vault_ids=("missing",), history_mode="none")
    assert database.get_vault_metadata().empty


def test_run_scan_rejects_empty_all_vault_snapshot(database: ApexMetricsDatabase, monkeypatch: MonkeyPatch) -> None:
    """Refuse to mark a populated database missing after an empty snapshot."""
    observed_at = datetime.datetime(2026, 7, 23, 12)
    database.apply_ranking((_vault("1"),), observed_at, manage_disappearance=True)
    monkeypatch.setattr("eth_defi.apex.metrics.fetch_stabilised_vaults", lambda *args, **kwargs: ())
    with pytest.raises(ApexAPIError, match="empty all-vault ranking"):
        run_scan(object(), database, history_mode="none")
    metadata = database.get_vault_metadata().iloc[0]
    assert pd.isna(metadata["missing_since"])
    assert len(database.get_vault_prices("1")) == 1


def test_run_scan_ranking_failure_leaves_database_untouched(database: ApexMetricsDatabase, monkeypatch: MonkeyPatch) -> None:
    """Propagate a ranking failure before mutating any database table."""

    def fail(*_args: object, **_kwargs: object) -> tuple[ApexVaultSummary, ...]:
        message = "ranking unavailable"
        raise ApexAPIError(message)

    monkeypatch.setattr("eth_defi.apex.metrics.fetch_stabilised_vaults", fail)
    with pytest.raises(ApexAPIError, match="ranking unavailable"):
        run_scan(object(), database, history_mode="none")
    assert database.get_vault_metadata().empty
    assert database.get_vault_prices().empty


def test_run_scan_records_each_nonterminal_invocation(database: ApexMetricsDatabase, monkeypatch: MonkeyPatch) -> None:
    """Record actual timestamps for every non-terminal scan invocation."""
    monkeypatch.setattr("eth_defi.apex.metrics.fetch_stabilised_vaults", lambda *args, **kwargs: (_vault("1"),))
    times = iter((datetime.datetime(2026, 7, 23, 12), datetime.datetime(2026, 7, 23, 12, 30)))
    monkeypatch.setattr("eth_defi.apex.metrics.native_datetime_utc_now", lambda: next(times))
    run_scan(object(), database, history_mode="none")
    run_scan(object(), database, history_mode="none")
    assert len(database.get_vault_prices("1")) == 2

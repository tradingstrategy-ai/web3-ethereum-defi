"""File-backed recovery of recorded policy inputs, including explicit zero caps."""

# ruff: noqa: S608 - Table and column identifiers are fixed fixture constants.

import datetime
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from eth_defi.hyperliquid.leader_share_recovery import recover_leader_shares
from eth_defi.hyperliquid.permission import PermissionObservation, append_permission_observation, initialise_permission_schema, project_permission_prices, read_permission_observations
from eth_defi.hyperliquid.vault_data_export import _compute_deposit_state_columns  # noqa: PLC2701 - Exercise the shared daily/HF export classifier.


@pytest.mark.parametrize("daily", [True, False])
def test_restore_policy_inputs_preserves_prices_and_unknowns(tmp_path: Path, *, daily: bool) -> None:  # noqa: PLR0914 - One scenario checks the transaction and export together.
    """Restore only missing inputs, with dry-run, verified backup and rerun safety.

    Conflicting scanner shares obey source order. Raw-only zero caps and retained
    evidence survive, genuinely missing fields remain null, and a later unknown
    response clears the export's carried policy inputs. Exercise old ART schemas
    on file-backed databases rather than an in-memory substitute.

    :param tmp_path: Isolated database and archive directory.
    :param daily: Exercise either daily or HF source keys.
    :return: ``None`` after validating the recovery and export contract.
    """
    table, clock, kind = ("vault_daily_prices", "date", "DATE") if daily else ("vault_high_freq_prices", "timestamp", "TIMESTAMP")
    address = "0x" + "a" * 40
    first = datetime.datetime(2026, 4, 11)  # noqa: DTZ001 - Scanner clocks are naive UTC.
    step = datetime.timedelta(days=1) if daily else datetime.timedelta(hours=1)
    clocks = [first + step * index for index in range(5)]
    target, newest, older = (tmp_path / name for name in ("target.duckdb", "newest.duckdb", "older.duckdb"))
    for path, shares in ((target, [None, None, None, 0.2, None]), (newest, [0.05, None, None, 1.00000000000007, None]), (older, [0.08, None, None, None, None])):
        connection = duckdb.connect(str(path))
        try:
            connection.execute(f"CREATE TABLE {table} (vault_address VARCHAR,{clock} {kind},share_price DOUBLE,tvl DOUBLE,is_closed BOOLEAN,allow_deposits BOOLEAN,leader_fraction DOUBLE,written_at TIMESTAMP,PRIMARY KEY(vault_address,{clock}))")
            connection.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?)", [(address, time, 1 + index, 100, False, True, share, first) for index, (time, share) in enumerate(zip(clocks, shares))])
            if path == target:
                initialise_permission_schema(connection)
                append_permission_observation(connection, PermissionObservation("legacy", address, permission_observed_at=first, is_closed=False, allow_deposits=True, relationship_type="normal", provenance="legacy_price_timestamp", payload_json=json.dumps({"leader_fraction": 0.05})))
                append_permission_observation(connection, PermissionObservation("unknown", address, permission_observed_at=clocks[1], provenance="observed_unknown"))
                connection.execute("CREATE TABLE hypercore_legacy_price_evidence (vault_address VARCHAR,source_timestamp TIMESTAMP,leader_fraction DOUBLE,original_row_json VARCHAR,source_path VARCHAR,source_sha256 VARCHAR)")
                connection.execute("INSERT INTO hypercore_legacy_price_evidence VALUES (?,?,?,?,?,?)", [address, clocks[4], 0.04, '{"max_deposit":0.0}', str(tmp_path / "backups/2026-09-29" / path.name), "retained-hash"])
                connection.execute("INSERT INTO hypercore_legacy_price_evidence VALUES (?,?,?,?,?,?)", [address, clocks[4], 0.08, "{}", str(tmp_path / "backups/2026-10-05/vault-prices-1h.parquet"), "retained-raw-hash"])
        finally:
            connection.close()
    raw = tmp_path / "vault-prices-1h.parquet"
    pd.DataFrame({"chain": [9999], "address": [address], "timestamp": [clocks[1]], "hypercore_source": ["daily" if daily else "hf"], "max_deposit": [0.0]}).to_parquet(raw)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    backup_dir = tmp_path / "own-backups"
    dry = recover_leader_shares(target, [newest, older], backup_dir, parquet_sources=[raw])
    assert (dry["restore_leader_fraction"], dry["restore_max_deposit"], dry["restore_observation_inputs"]) == (2, 2, 1)
    assert dry["conflicting_share_keys"] == 2  # noqa: PLR2004 - One attached archive conflict and one retained scanner/raw conflict.
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    assert not backup_dir.exists()
    report = recover_leader_shares(target, [newest, older], backup_dir, parquet_sources=[raw], dry_run=False)
    assert hashlib.sha256(Path(report["backup"]["backup"]).read_bytes()).hexdigest() == digest
    connection = duckdb.connect(str(target), read_only=True)
    try:
        assert connection.execute(f"SELECT leader_fraction,max_deposit FROM {table} ORDER BY {clock}").fetchall() == [(0.05, None), (None, 0.0), (None, None), (0.2, None), (0.04, 0.0)]
        assert connection.execute(f"SELECT share_price,tvl,is_closed,allow_deposits,written_at FROM {table} ORDER BY {clock}").fetchall() == [(1 + index, 100, False, True, first) for index in range(5)]
        assert connection.execute("SELECT count(*) FROM duckdb_constraints() WHERE database_name=current_database() AND constraint_type IN ('PRIMARY KEY','UNIQUE')").fetchone()[0] == 0
        prices = connection.execute(f"SELECT * FROM {table} ORDER BY {clock}").df()
        projected = project_permission_prices(prices, read_permission_observations(connection), clock)
        _, _, caps = _compute_deposit_state_columns(projected, projected.leader_fraction)
        assert projected.iloc[0].leader_fraction == pytest.approx(0.05)
        assert caps.iloc[0] == 0
        assert pd.isna(projected.iloc[0].capacity_observed_at)
        assert projected.leader_fraction.iloc[1:].isna().all()
        assert caps.iloc[1:].isna().all()
        observations_before = connection.execute("SELECT * FROM vault_permission_observations ORDER BY observation_id").fetchall()
        audit_before = connection.execute("SELECT * FROM hypercore_leader_share_recovery ORDER BY source_timestamp,source_sha256").fetchall()
    finally:
        connection.close()
    repeated = recover_leader_shares(target, [newest, older], backup_dir, parquet_sources=[raw], dry_run=False)
    assert repeated["restore_leader_fraction"] == repeated["restore_max_deposit"] == repeated["restore_observation_inputs"] == 0
    connection = duckdb.connect(str(target), read_only=True)
    try:
        assert connection.execute("SELECT * FROM vault_permission_observations ORDER BY observation_id").fetchall() == observations_before
        assert connection.execute("SELECT * FROM hypercore_leader_share_recovery ORDER BY source_timestamp,source_sha256").fetchall() == audit_before
    finally:
        connection.close()


@pytest.mark.parametrize("clock", ["date", "timestamp"])
def test_archived_policy_projection_keeps_coherent_values(clock: str) -> None:
    """Carry archived policy snapshots without mixing inputs or requiring a receipt.

    A higher archived share clears the old zero cap. A later explicit zero
    without a share replaces both inputs coherently and remains usable.

    :param clock: Daily or HF source clock column.
    :return: ``None`` after checking projected shares and exported policy caps.
    """
    prices = pd.DataFrame({"vault_address": ["0x" + "a" * 40] * 6, clock: pd.date_range("2026-04-11", periods=6, freq="D"), "leader_fraction": [0.03, None, 0.10, None, None, None], "max_deposit": [0.0, None, None, None, 0.0, None]})
    projected = project_permission_prices(prices, pd.DataFrame(), clock)
    _, _, caps = _compute_deposit_state_columns(projected, projected.leader_fraction)
    assert projected.leader_fraction.iloc[:4].tolist() == [0.03, 0.03, 0.10, 0.10]
    assert projected.leader_fraction.iloc[4:].isna().all()
    assert caps.iloc[[0, 1, 4, 5]].eq(0.0).all()
    assert caps.iloc[2:4].isna().all()

"""Test the looped vault scanner with Lighter protocol.

Verifies that the cycle-based loop mode works end-to-end:

1. Set up isolated temp directory via PIPELINE_DATA_DIR
2. Call main() from eth_defi.vault.scan_all_chains with MAX_CYCLES=1, Lighter only
3. Verify cycle state JSON records successful completion
4. Verify Lighter DuckDB has pool data
5. Verify vault-metadata-db.pickle contains Lighter vaults
"""

import json
import pickle
from pathlib import Path

import duckdb
import pytest

from eth_defi.vault.scan_all_chains import main


@pytest.mark.timeout(180)
def test_scan_loop_lighter_single_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run one looped cycle scanning only Lighter and verify output files.

    1. Set env vars so main() uses tmp_path as PIPELINE_DATA_DIR
    2. Exclude all EVM chains via TEST_CHAINS=none
    3. Call main() directly with MAX_CYCLES=1
    4. Check cycle state JSON was written with Lighter success timestamp
    5. Check DuckDB and pickle files exist and have content
    """

    # Set env vars — get_pipeline_data_dir() reads PIPELINE_DATA_DIR at call time
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOOP_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("MAX_CYCLES", "1")
    monkeypatch.setenv("SCAN_CYCLES", "Lighter=0h")
    monkeypatch.setenv("DEFAULT_CYCLE", "24h")
    monkeypatch.setenv("TEST_CHAINS", "none")  # No EVM chains
    monkeypatch.setenv("SCAN_PRICES", "false")
    monkeypatch.setenv("SCAN_HYPERCORE", "false")
    monkeypatch.setenv("SCAN_GRVT", "false")
    monkeypatch.setenv("SCAN_LIGHTER", "true")
    monkeypatch.setenv("SKIP_POST_PROCESSING", "true")
    monkeypatch.setenv("MAX_WORKERS", "4")
    monkeypatch.setenv("LOG_LEVEL", "info")

    main()

    # 1. Verify cycle state JSON was written
    state_file = tmp_path / "scan-cycle-state.json"
    assert state_file.exists(), "Cycle state file not created"
    state = json.loads(state_file.read_text())
    assert "Lighter" in state, f"Lighter not in cycle state: {state}"

    # 2. Verify Lighter DuckDB has pools
    duckdb_path = tmp_path / "lighter-pools.duckdb"
    assert duckdb_path.exists(), "Lighter DuckDB not created"

    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        pool_count = con.execute("SELECT count(*) FROM pool_metadata").fetchone()[0]
        assert pool_count > 0, "No pools in DuckDB"
    finally:
        con.close()

    # 3. Verify vault metadata pickle contains Lighter vaults
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    assert vault_db_path.exists(), "Vault DB pickle not created"
    vault_db = pickle.load(vault_db_path.open("rb"))
    lighter_vaults = [k for k in vault_db.rows.keys() if hasattr(k, "vault_address") and str(k.vault_address).startswith("lighter-pool-")]
    assert len(lighter_vaults) > 0, f"No Lighter vaults in vault DB, keys: {list(vault_db.rows.keys())[:5]}"

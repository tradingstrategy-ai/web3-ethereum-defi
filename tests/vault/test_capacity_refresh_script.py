"""Manual refresh uses production persistence and preserves its read-only source."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from eth_defi.vault import withdrawal_capacity_refresh
from eth_defi.vault.vaultdb import VaultDatabase

SCRIPT = Path(__file__).parents[2] / "scripts/erc-4626/refresh-withdrawal-capacity.py"
spec = importlib.util.spec_from_file_location("capacity_refresh_script", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
ADDRESS = "0x" + "a" * 40
SECOND = "0x" + "b" * 40


def write_source(path: Path) -> None:
    """Publish two Monad candidates and one out-of-scope chain candidate."""
    rows = [{"chain_id": chain, "address": address, "name": address, "protocol": "Fixture", "denomination": "USDC", "current_nav": 10000, "features": []} for chain, address in [(143, ADDRESS), (143, SECOND), (1, ADDRESS)]]
    path.write_text(json.dumps({"vaults": rows}), encoding="utf-8")


@pytest.mark.parametrize("max_batches,refreshed,remaining", [(1, 1, 1), (2, 2, 0)])
def test_manual_refresh_records_batches_and_reuses_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_batches: int, refreshed: int, remaining: int) -> None:
    """Use real selection, persistence and export with only the RPC adapter replaced."""
    source = tmp_path / "source.json"
    write_source(source)
    original = source.read_bytes()
    state_dir = tmp_path / "diagnostic"
    now = datetime.now(UTC)
    calls = []
    provider = SimpleNamespace(eth=SimpleNamespace(chain_id=143, get_block=lambda number: {"number": 126 if number == "latest" else number, "hash": bytes(32), "timestamp": int(now.timestamp())}))

    def construct(web3: SimpleNamespace, address: str, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["default_block_identifier"] == 123
        calls.append(address)
        tvl = "10000" if address == ADDRESS else "1000"
        return SimpleNamespace(fetch_withdrawal_capacity_observation=lambda number: {"status": "measured", "current_capacity": "1000", "current_tvl": tvl, "current_tvl_usd": tvl, "denomination_symbol": "USDC", "deposits_paused": False, "withdrawals_paused": False})

    monkeypatch.setattr(runner, "create_multi_provider_web3", lambda url: provider)
    monkeypatch.setattr(runner, "read_json_rpc_url", lambda chain: "unused-test-provider")
    monkeypatch.setattr(withdrawal_capacity_refresh, "create_vault_instance", construct)
    report = runner.run_refresh(source, state_dir, 143, published_json=True, max_batches=max_batches, batch_size=1)
    assert report["refreshed"] == refreshed and report["remaining_due"] == remaining
    assert report["vault_count"] == 2 and len(calls) == refreshed
    assert report["read_counts"].get("unchecked", 0) == remaining
    assert source.read_bytes() == original
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    persisted = VaultDatabase.read(state_dir / "vault-metadata-db.pickle")
    assert all(spec.chain_id == 143 for spec in persisted.rows)
    assert sum("_withdrawal_capacity_observation" in row for row in persisted.rows.values()) == refreshed
    if remaining == 0:
        assert report["cadence_counts"] == {"86400": 1, "604800": 1}
        monkeypatch.setattr(runner, "create_multi_provider_web3", lambda url: pytest.fail("Fresh state constructed a provider"))
        resumed = runner.run_refresh(source, state_dir, 143, published_json=True, max_batches=2)
        assert resumed["refreshed"] == 0 and resumed["batches"] == 0
        assert resumed["rows"] == report["rows"]
        assert "\033[31m✗" in runner.render_table(resumed, colour=True)
        assert "Next check UTC" in runner.render_table(resumed)
        source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
        unchanged = (state_dir / "vault-metadata-db.pickle").read_bytes()
        cached = runner.run_refresh(source, state_dir, 143, published_json=True)
        assert cached["source_sha256"] == report["source_sha256"]
        assert cached["rows"] == report["rows"]
        assert (state_dir / "vault-metadata-db.pickle").read_bytes() == unchanged


def test_manual_refresh_rejects_source_output_collision(tmp_path: Path) -> None:
    """The input cannot be overwritten by either catalogue or manifest output."""
    source = tmp_path / "input.json"
    write_source(source)
    original = source.read_bytes()
    with pytest.raises(AssertionError, match="read-only"):
        runner.run_refresh(source, tmp_path, 143, published_json=True)
    assert source.read_bytes() == original


def test_pickle_snapshot_resumes_after_live_source_disappears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Already-fresh pickle observations survive source rotation without RPC reads."""
    source = tmp_path / "live.pickle"
    database = VaultDatabase()
    now = datetime.now(UTC).replace(tzinfo=None)
    database.rows[runner.VaultSpec(143, ADDRESS)] = {"Name": "Fresh", "Denomination": "USDC", "NAV": 10000, "features": set(), "_withdrawal_capacity_observation": {"status": "measured", "current_capacity": "1000", "current_tvl": "10000", "observed_at": now.isoformat(), "checked_at": now.isoformat(), "refresh_interval_seconds": 86400}}
    database.rows[runner.VaultSpec(1, ADDRESS)] = {"Name": "Other chain"}
    database.write(source)
    monkeypatch.setattr(runner, "create_multi_provider_web3", lambda url: pytest.fail("Fresh source opened an RPC provider"))
    state = tmp_path / "isolated"
    first = runner.run_refresh(source, state, 143)
    assert first["vault_count"] == 1 and first["refreshed"] == 0
    source.unlink()
    resumed = runner.run_refresh(source, state, 143)
    assert resumed["rows"] == first["rows"] and resumed["source_sha256"] == first["source_sha256"]


def test_json_nav_keeps_accounting_denomination() -> None:
    """Converted export NAV must use its accounting unit in the cadence fallback."""
    content = json.dumps({"vaults": [{"chain_id": 143, "address": ADDRESS, "name": "Converted", "current_nav": 10000, "denomination": "USDC", "source_denomination": "WMON", "features": []}]}).encode()
    row = runner.load_snapshot(content, 143, published_json=True).rows[runner.VaultSpec(143, ADDRESS)]
    assert row["Denomination"] == "USDC" and row["_source_denomination"] == "WMON"
    assert withdrawal_capacity_refresh.get_capacity_refresh_interval(row).total_seconds() == 86400


@pytest.mark.parametrize("error,expected", [(AssertionError("Use an isolated STATE_DIR"), "Use an isolated STATE_DIR"), (RuntimeError("https://credential@example.invalid"), "RuntimeError"), (AssertionError("https://credential@example.invalid"), "URL-bearing details redacted")])
def test_cli_preserves_local_messages_but_redacts_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str) -> None:
    """Own safety refusals remain actionable while provider exception URLs stay private."""

    def fail(*args: Any, **kwargs: Any) -> dict:
        raise error

    for setting in ("COLOUR", "CHAIN_ID", "VAULT_METADATA_JSON", "MAX_BATCHES", "MAX_VAULTS_PER_CHAIN", "MAX_WORKERS"):
        monkeypatch.delenv(setting, raising=False)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(runner, "run_refresh", fail)
    with pytest.raises(SystemExit) as result:
        runner.main()
    assert expected in str(result.value)
    assert "credential" not in str(result.value)


def test_pipeline_directory_is_rejected_before_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The manual runner cannot target the active scanner catalogue directory."""
    pipeline = tmp_path / "pipeline"
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(pipeline))
    with pytest.raises(AssertionError, match="isolated"):
        runner.run_refresh(tmp_path / "not-read.json", pipeline, 143, published_json=True)
    assert not pipeline.exists()

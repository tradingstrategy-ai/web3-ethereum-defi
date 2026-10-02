"""Standalone scanner accounting at successful, failed and cancelled boundaries."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.vault.rpc_scan_state import save_rpc_scan_state


@pytest.mark.parametrize(("failure", "pending_candidates"), [(None, 0), (None, 1), (RuntimeError("scan failed"), 0), (KeyboardInterrupt(), 0)], ids=["completed", "degraded", "failed", "cancelled"])
def test_standalone_discovery_retains_attempts_and_original_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException | None, pending_candidates: int) -> None:
    """Real file-backed accounting survives a discovery failure or cancellation.

    Only network reads are replaced. Running the entrypoint's actual lock and
    accounting boundary checks that removing redundant exception wrappers still
    preserves partial requests, labels failed work honestly, and propagates the
    original exception. The existing repair override must reach both core caches.

    :param tmp_path: Isolated pipeline and accounting files.
    :param monkeypatch: Replaces script configuration and network calls.
    :param failure: Discovery failure, cancellation, or None for completed work.
    :param pending_candidates: Deferred metadata after a successful discovery.
    :return: None; verifies persisted attempts, outcomes and repair arguments.
    """
    counter_path = tmp_path / "rpc-tracking.duckdb"
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path / "pipeline"))
    monkeypatch.setenv("RPC_TRACKING_DATABASE_PATH", str(counter_path))
    monkeypatch.setenv("JSON_RPC_URL", "https://rpc.example")
    monkeypatch.setenv("HYPERSYNC_API_KEY", "test-placeholder")
    monkeypatch.setenv("FORCE_LEAD_DISCOVERY", "true")
    script = Path(__file__).parents[2] / "scripts/erc-4626/scan-vaults.py"
    main = runpy.run_path(str(script))["main"]
    namespace = main.__globals__
    monkeypatch.setitem(namespace, "setup_console_logging", lambda **kwargs: None)

    def create_web3(json_rpc_urls: str, rpc_request_stats: RPCRequestStats) -> SimpleNamespace:
        """Supply a verified chain while retaining its initial physical attempt.

        :param json_rpc_urls: Script-selected fallback configuration.
        :param rpc_request_stats: Actual phase accumulator used by the entrypoint.
        :return: Minimal Web3 stand-in with the chain identity required by accounting.
        """
        assert json_rpc_urls == "https://rpc.example"
        rpc_request_stats.record_call("rpc.example", "eth_chainId")
        return SimpleNamespace(eth=SimpleNamespace(chain_id=1))

    def fetch_leads(**kwargs: Any) -> SimpleNamespace:
        """Record partial work before simulating the discovery boundary.

        :param kwargs: Arguments forwarded by the standalone script.
        :return: Completed discovery report when no failure is selected.
        """
        assert kwargs["force_metadata_refresh"] is True
        assert kwargs["force_classification_refresh"] is True
        assert kwargs["vault_db_file"] == tmp_path / "pipeline" / namespace["DEFAULT_VAULT_DATABASE"].name
        kwargs["rpc_request_stats"].record_call("rpc.example", "eth_call")
        if failure is not None:
            raise failure
        pending = {f"0x{number:040x}": {} for number in range(pending_candidates)}
        save_rpc_scan_state(tmp_path / "pipeline" / "rpc-pending-metadata-1.json", pending)
        return SimpleNamespace(items_scanned=7)

    monkeypatch.setitem(namespace, "create_multi_provider_web3", create_web3)
    monkeypatch.setitem(namespace, "scan_leads", fetch_leads)
    if failure is None:
        main()
    else:
        with pytest.raises(type(failure)) as caught:
            main()
        assert caught.value is failure

    with duckdb.connect(str(counter_path), read_only=True) as connection:
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 2
        outcome, metrics = connection.execute("SELECT outcome, metrics FROM vault_rpc_operation_calls WHERE operation='outcome'").fetchone()
        assert outcome == ("failed" if failure is not None else "degraded" if pending_candidates else "completed")
        diagnostics = json.loads(metrics)
        assert diagnostics["items_scanned"] == (7 if failure is None else 0)
        assert diagnostics.get("pending_candidates", 0) == pending_candidates
        if failure is not None:
            assert "error" not in diagnostics

"""Reader-state diagnostics support current and legacy persisted formats."""

import logging
import pickle
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from eth_defi.vault.base import VaultSpec

SCRIPT = Path(__file__).parents[2] / "scripts/erc-4626/check-reader-states.py"


@pytest.mark.parametrize("legacy", [False, True])
def test_reader_state_diagnostics_report_recorded_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, legacy: bool) -> None:
    """The helper reads actual pipeline dictionaries as well as legacy objects."""
    address = "0x" + "1" * 40
    status = {"maxDeposit": (123, True), "totalAssets": (124, False)}
    spec = (1, address) if legacy else VaultSpec(1, address)
    state = SimpleNamespace(call_status=status) if legacy else {"call_status": status, "last_block": 999}
    path = tmp_path / "vault-reader-state-1h.pickle"
    path.write_bytes(pickle.dumps({spec: state}))
    before = path.read_bytes()
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("READER_STATE_PATH", raising=False)
    main = runpy.run_path(str(SCRIPT))["main"]
    monkeypatch.setitem(main.__globals__, "setup_console_logging", lambda _level: None)
    caplog.set_level(logging.INFO)
    main()
    assert address in caplog.text and "maxDeposit" in caplog.text
    assert "totalAssets" not in caplog.text
    assert "recorded checks: 2; broken calls: 1" in caplog.text
    assert "Unchecked calls have unknown status" in caplog.text
    assert path.read_bytes() == before

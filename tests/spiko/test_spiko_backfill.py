"""Regression tests for the address-scoped Spiko USTBL migration."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.tokenised_fund.spiko.constants import USTBL_ORACLE_FIRST_SEEN_AT_BLOCK
from eth_defi.vault.base import VaultSpec


@pytest.fixture
def backfill_history_module():
    """Load the migration script as a module.

    :return: Imported script module.
    """
    script_path = Path(__file__).parents[2] / "scripts" / "spiko" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("spiko_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spiko_backfill_starts_at_oracle(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Avoid manufacturing price rows before the verified NAV oracle exists."""
    monkeypatch.delenv("START_BLOCK", raising=False)
    assert backfill_history_module.resolve_start_block() == USTBL_ORACLE_FIRST_SEEN_AT_BLOCK


def test_spiko_backfill_rejects_hourly_fill_forward(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Keep daily NAV reconciliation at daily scanner frequency."""
    monkeypatch.setenv("FREQUENCY", "1h")
    with pytest.raises(ValueError, match="only FREQUENCY=1d"):
        backfill_history_module.resolve_frequency()


def test_spiko_backfill_reader_state_round_trip(tmp_path: Path, backfill_history_module) -> None:
    """Persist unrelated reader state without changing its mapping."""
    path = tmp_path / "reader-state.pickle"
    unrelated = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    backfill_history_module.write_reader_states(path, {unrelated: {"last_block": 123}})
    assert backfill_history_module.read_reader_states(path) == {unrelated: {"last_block": 123}}


def test_spiko_backfill_detection_uses_spiko_feature(backfill_history_module) -> None:
    """Build the hardcoded USTBL classification used by the migration."""
    assert backfill_history_module.create_spiko_detection().features == {ERC4626Feature.spiko_like}

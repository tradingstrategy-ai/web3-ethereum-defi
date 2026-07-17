"""Regression tests for the targeted Circle USYC backfill script."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.tokenised_fund.usyc.constants import USYC_ORACLE_FIRST_SEEN_AT_BLOCK
from eth_defi.vault.base import VaultSpec


@pytest.fixture
def backfill_history_module():
    """Load the hyphenated USYC backfill script as a Python module."""
    script_path = Path(__file__).parents[2] / "scripts" / "usyc" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("usyc_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_usyc_backfill_defaults_to_oracle_deployment(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Avoid emitting pre-oracle USYC rows without a verified NAV source."""
    monkeypatch.delenv("START_BLOCK", raising=False)
    assert backfill_history_module.resolve_start_block() == USYC_ORACLE_FIRST_SEEN_AT_BLOCK


def test_usyc_backfill_rejects_hourly_fill_forward(monkeypatch: pytest.MonkeyPatch, backfill_history_module) -> None:
    """Keep one daily sample for each business-day oracle reconciliation."""
    monkeypatch.setenv("FREQUENCY", "1h")
    with pytest.raises(ValueError, match="only FREQUENCY=1d"):
        backfill_history_module.resolve_frequency()


def test_usyc_backfill_reader_state_round_trip(tmp_path: Path, backfill_history_module) -> None:
    """Preserve unrelated state mappings when the USYC entry is removed."""
    path = tmp_path / "reader-state.pickle"
    unrelated = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    backfill_history_module.write_reader_states(path, {unrelated: {"last_block": 123}})
    assert backfill_history_module.read_reader_states(path) == {unrelated: {"last_block": 123}}


def test_usyc_backfill_detection_uses_usyc_feature(backfill_history_module) -> None:
    """Generate a hardcoded classification record for the USYC adapter."""
    assert backfill_history_module.create_usyc_detection().features == {ERC4626Feature.usyc_like}

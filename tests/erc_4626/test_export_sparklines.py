"""Test sparkline export inclusion policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from eth_defi.vault.base import VaultSpec


def _load_export_sparklines_module():
    """Load the standalone sparkline export script as a Python module.

    The production scanner imports this script dynamically, so this test loads
    the same file rather than duplicating its inclusion policy.

    :return:
        Imported sparkline export module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "export-sparklines.py"
    spec = importlib.util.spec_from_file_location("export_sparklines", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_vault_row(spec: VaultSpec, protocol: str) -> dict:
    """Create a minimal stablecoin vault row for sparkline selection tests.

    The selector needs only a persisted specification, protocol display name,
    and denomination; other scanner metadata is unrelated to TVL eligibility.

    :param spec:
        Synthetic vault identity.
    :param protocol:
        Protocol display name emitted by the scanner.
    :return:
        Minimal compatible vault metadata row.
    """
    return {
        "Protocol": protocol,
        "Denomination": "USDT",
        "_detection_data": SimpleNamespace(get_spec=lambda: spec),
    }


def test_apex_sparkline_threshold_exemption() -> None:
    """Include USDT 500 ApeX vaults without lowering the global threshold.

    ApeX is intentionally exempt while it is new and has little TVL. A
    non-ApeX vault at the same TVL remains below the standard threshold, and
    ApeX still needs to reach the lower USDT 500 floor.

    :return:
        None. Assertions validate the peak-TVL inclusion policy.
    """
    module = _load_export_sparklines_module()
    apex_eligible = VaultSpec(9995, "apex-vault-eligible")
    apex_below_floor = VaultSpec(9995, "apex-vault-below-floor")
    non_apex_small = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    non_apex_large = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    vault_db = SimpleNamespace(
        rows={
            apex_eligible: _make_vault_row(apex_eligible, "ApeX"),
            apex_below_floor: _make_vault_row(apex_below_floor, "ApeX"),
            non_apex_small: _make_vault_row(non_apex_small, "Other protocol"),
            non_apex_large: _make_vault_row(non_apex_large, "Other protocol"),
        }
    )
    prices_df = pd.DataFrame(
        {
            "id": [
                apex_eligible.as_string_id(),
                apex_below_floor.as_string_id(),
                non_apex_small.as_string_id(),
                non_apex_large.as_string_id(),
            ],
            "total_assets": [500, 499, 500, 5000],
        }
    )

    included = module.get_included_vault_ids(vault_db, prices_df)

    assert included == {apex_eligible.as_string_id(), non_apex_large.as_string_id()}

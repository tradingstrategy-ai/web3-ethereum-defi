"""Test standalone sparkline export orchestration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest
from joblib import Parallel, delayed

from eth_defi.research.sparkline import prepare_sparkline_data
from eth_defi.vault.base import VaultSpec


@pytest.fixture(scope="module")
def export_sparklines_module() -> ModuleType:
    """Load the standalone exporter using the production import mechanism.

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
    """Create a minimal stablecoin vault row for inclusion tests.

    :param spec:
        Synthetic vault identity.
    :param protocol:
        Protocol display name.
    :return:
        Minimal compatible vault metadata row.
    """
    return {
        "Protocol": protocol,
        "Denomination": "USDT",
        "_detection_data": SimpleNamespace(get_spec=lambda: spec),
    }


def test_apex_sparkline_threshold_exemption(export_sparklines_module: ModuleType) -> None:
    """Include USD 500 ApeX vaults without lowering the global threshold.

    :param export_sparklines_module:
        Dynamically loaded standalone exporter module.
    :return:
        None. Assertions validate the peak-TVL inclusion policy.
    """
    module = export_sparklines_module
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


def test_rendered_images_cross_joblib_process_boundary(export_sparklines_module: ModuleType) -> None:
    """Return SVG and PNG dictionaries from standalone-script workers.

    :param export_sparklines_module:
        Dynamically loaded standalone exporter module.
    :return:
        None. Assertions validate Loky serialisation and image formats.
    """
    module = export_sparklines_module
    index = pd.date_range("2026-08-01", periods=15, freq="D", name="timestamp")
    prices_df = pd.DataFrame(
        {"share_price": [1.0] * len(index), "total_assets": [10_000.0] * len(index)},
        index=index,
    )
    sparkline_data = prepare_sparkline_data(prices_df)
    assert sparkline_data is not None

    results = Parallel(n_jobs=2, prefer="processes")(delayed(module.render_vault_sparklines)(f"vault-{index}", sparkline_data) for index in range(2))

    assert [[image["extension"] for image in vault_images] for vault_images in results] == [["svg", "png"], ["svg", "png"]]
    assert all(vault_images[0]["payload"].startswith(b"<?xml") for vault_images in results)
    assert all(vault_images[1]["payload"].startswith(b"\x89PNG") for vault_images in results)

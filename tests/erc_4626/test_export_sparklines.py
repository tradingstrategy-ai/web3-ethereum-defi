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


def _make_vault_row(spec: VaultSpec, denomination: str) -> dict:
    """Create a minimal vault row for inclusion tests.

    :param spec:
        Synthetic vault identity.
    :param denomination:
        Persisted denomination symbol.
    :return:
        Minimal compatible vault metadata row.
    """
    return {
        "Protocol": "Example",
        "Denomination": denomination,
        "_detection_data": SimpleNamespace(get_spec=lambda: spec),
    }


def test_sparkline_inclusion_covers_supported_families_without_peak_gate(export_sparklines_module: ModuleType) -> None:
    """Include stablecoin, ETH and BTC vaults based on finite input rows.

    :param export_sparklines_module:
        Dynamically loaded standalone exporter module.
    :return:
        None. Assertions validate the family-based inclusion policy.
    """
    module = export_sparklines_module
    stablecoin = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    eth = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    btc = VaultSpec(1, "0x0000000000000000000000000000000000000003")
    unsupported = VaultSpec(1, "0x0000000000000000000000000000000000000004")
    vault_db = SimpleNamespace(
        rows={
            stablecoin: _make_vault_row(stablecoin, "USDC"),
            eth: _make_vault_row(eth, "WETH"),
            btc: _make_vault_row(btc, "WBTC"),
            unsupported: _make_vault_row(unsupported, "SOL"),
        }
    )
    index = pd.date_range("2026-01-01", periods=15, freq="D", name="timestamp")
    ids = [stablecoin.as_string_id(), eth.as_string_id(), btc.as_string_id(), unsupported.as_string_id()]
    prices_df = pd.DataFrame(
        {
            "id": [vault_id for _ in index for vault_id in ids],
            "share_price": [1.0] * (len(ids) * len(index)),
            "total_assets": [value for _ in index for value in (1, 0.01, 0.001, 10_000)],
        },
        index=index.repeat(len(ids)),
    ).sort_index()

    included = module.get_included_vault_ids(vault_db, prices_df)

    assert included == {stablecoin.as_string_id(), eth.as_string_id(), btc.as_string_id()}


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

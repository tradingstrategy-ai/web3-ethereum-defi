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
    """Expose the standalone exporter to its integration tests."""
    return _load_sparkline_script("export-sparklines")


def _load_sparkline_script(name: str) -> ModuleType:
    """Load the standalone exporter using the production import mechanism.

    :param name: Script filename without its extension.
    :return: Imported sparkline script.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / f"{name}.py"
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

    assert [[image["extension"] for image in vault_images] for vault_images in results] == [["svg", "png", "png"], ["svg", "png", "png"]]
    assert all(vault_images[0]["payload"].startswith(b"<?xml") for vault_images in results)
    assert all(vault_images[1]["payload"].startswith(b"\x89PNG") for vault_images in results)


def test_worker_backs_off_after_snapshot_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistent input or configuration error uses the slower retry interval."""
    module = _load_sparkline_script("scan-vault-sparklines")

    def fail_export(**_: object) -> None:
        message = "invalid snapshot"
        raise ValueError(message)

    delays = []

    def stop_after_delay(seconds: int) -> None:
        delays.append(seconds)
        raise StopIteration

    monkeypatch.setattr(module, "export_queued_sparklines", lambda _data_dir, **kwargs: fail_export(**kwargs))
    monkeypatch.setattr(module.time, "sleep", stop_after_delay)
    with pytest.raises(StopIteration):
        module.run_worker(tmp_path)
    assert delays == [300]


@pytest.mark.parametrize("staged", [False, True])
def test_manual_export_picks_staged_inputs_without_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_sparklines_module: ModuleType,
    *,
    staged: bool,
) -> None:
    """Use the staged snapshot automatically and retain the standalone fallback."""
    module = export_sparklines_module
    calls = []
    result = SimpleNamespace(success=True)
    monkeypatch.setattr(module, "setup_console_logging", lambda **_: None)
    monkeypatch.setattr(module, "get_pipeline_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "export_queued_sparklines", lambda *_args, **_kwargs: ("snapshot", result) if staged else None)
    monkeypatch.setattr(module, "run_sparkline_export", lambda **_: calls.append("inline") or result)
    module.main()
    assert calls == ([] if staged else ["inline"])

"""Scheduled collection, private admission and shared-history regression tests."""

import datetime
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from eth_defi.neutral_trade.api import parse_vault
from eth_defi.neutral_trade.constants import is_public_metadata
from eth_defi.neutral_trade.metrics import NeutralTradeDatabase, run_scan
from eth_defi.neutral_trade.vault_data_export import build_raw_prices_dataframe, merge_into_vault_database
from eth_defi.vault.post_processing import merge_native_protocols
from eth_defi.vault.scan_all_chains import build_active_protocols, ensure_default_scan_cycles

ADDRESS = "J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ"


def test_bounded_scan_resumes_and_hides_revoked_prices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run discovery and page collection through the real transactional writer.

    A page budget yields pending state. Later collection resumes the cursor;
    hiding the vault removes its public prices while retaining private history.

    :param tmp_path: Isolated source and shared output paths.
    :param monkeypatch: Deterministic HTTP boundary replacements.
    """
    raw = {"bundleKey": ADDRESS, "chain": "solana", "name": "Fixture", "visible": True, "private": False, "asset": {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC", "decimals": 6}}
    seen_positions = []

    @contextmanager
    def client(_key: str):
        yield SimpleNamespace(fetch_status=lambda: {}, fetch_vaults=lambda: iter([parse_vault(raw)]), fetch_metrics=lambda _vault: {"data": {}})

    def fetch(_key: str, vault: object, endpoint: str, position: tuple) -> tuple:
        seen_positions.append(position)
        if endpoint == "share-price":
            data = {"vaultId": ADDRESS, "nextCursor": "second" if position[0] is None else None, "series": [{"blockTime": 1794465911, "pps": "1100000", "totalEquity": "11000000000", "totalShares": "10000000000", "isLegacy": False, "signature": "fixture", "slot": 1, "sourceEvent": "OracleUpdated"}]}
        else:
            data = {"vaultId": ADDRESS, "nextCursor": None, "snapshots": []}
        return vault, endpoint, {"data": data}, None

    monkeypatch.setattr("eth_defi.neutral_trade.metrics.NeutralTradeClient", client)
    monkeypatch.setattr("eth_defi.neutral_trade.metrics.fetch_history_page", fetch)
    path = tmp_path / "source.duckdb"
    metadata = tmp_path / "metadata.pickle"
    with NeutralTradeDatabase(path) as db:
        first = run_scan(db, "synthetic-key", page_budget=1)
        assert first.pending and first.pages == 1 and not first.failures
        second = run_scan(db, "synthetic-key", page_budget=10)
        assert not second.pending and not second.failures
        assert any(position[0] == "second" for position in seen_positions)
        merge_into_vault_database(db, metadata)
    prices = tmp_path / "raw.parquet"
    assert merge_native_protocols(merge_neutral_trade=True, neutral_trade_db_path=path, uncleaned_parquet_path=prices)["neutral-trade-price-merge"]
    with NeutralTradeDatabase(path) as db:
        raw["visible"] = False
        run_scan(db, "synthetic-key")
        assert build_raw_prices_dataframe(db).empty
        assert db.con.execute("SELECT count(*) FROM observations").fetchone()[0] > 0
        retained = merge_into_vault_database(db, metadata)
        assert len(retained.rows) == 1
        assert not is_public_metadata(next(iter(retained.rows.values())))
    merge_native_protocols(merge_neutral_trade=True, neutral_trade_db_path=path, uncleaned_parquet_path=prices)
    assert pq.read_table(prices).num_rows == 1  # Archival history survives revocation.


def test_scan_scheduling_defaults() -> None:
    """Register the opt-in source with a four-hour cadence.

    Explicit operator intervals retain priority over built-in defaults.
    """
    active = build_active_protocols(False, False, False, False, False, False, False, scan_neutral_trade=True)
    assert active == ["Neutral Trade"]
    assert ensure_default_scan_cycles({})["Neutral Trade"] == datetime.timedelta(hours=4)
    assert ensure_default_scan_cycles({"Neutral Trade": datetime.timedelta(hours=1)})["Neutral Trade"] == datetime.timedelta(hours=1)

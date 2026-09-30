"""Antarctic metadata and real event context through Parquet and vault JSON."""

import datetime
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyarrow.parquet as pq
import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import AntarcticHistoricalContextStore
from eth_defi.erc_4626.vault_protocol.antarctic.vault import AntarcticVault
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics
from eth_defi.testing.antarctic import RecordedAntarcticProvider, create_antarctic_test_metadata, load_antarctic_settlements, write_antarctic_test_prices
from eth_defi.token import TokenDiskCache
from eth_defi.vault import historical, top_vaults_json
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader, VaultSpec
from eth_defi.vault.post_processing import clean_prices

FIXTURES = Path(__file__).parent / "fixtures"


def test_antarctic_parquet_and_vault_json_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0914
    """Both products reach real cleaning, metrics and strict JSON publication."""
    records = load_antarctic_settlements(FIXTURES / "antarctic-settlements.json")
    web3 = Web3(RecordedAntarcticProvider(FIXTURES / "antarctic-rpc.json"))
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    start = min(r.block_number for r in records)
    end = max(r.block_number for r in records) + 1
    try:
        database = create_antarctic_test_metadata(web3, cache)
        metadata = tmp_path / "vault-metadata-db.pickle"
        database.write(metadata)
        with AntarcticHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
            for deployment in ANTARCTIC_DEPLOYMENTS:
                store.replace_range(deployment.address, start, end, iter(r for r in records if r.pool_address == deployment.address))
        result = write_antarctic_test_prices(web3, cache, tmp_path, start, end)
        assert result["rows_written"] > 0
        raw = pq.read_table(tmp_path / "vault-prices-1h.parquet").to_pandas()
        assert set(raw["address"]) == {d.address for d in ANTARCTIC_DEPLOYMENTS}
        assert raw["total_supply"].isna().all() and raw["management_fee"].isna().all()
        assert raw["perp_long_notional"].isna().all()
        assert set(raw["block_number"]).issubset({r.block_number for r in records if r.kind == "AddLiquidity"})
        assert raw["block_number"].max() == 505781464  # noqa: PLR2004
        cleaned = tmp_path / "cleaned-vault-prices-1h.parquet"
        assert clean_prices(vault_db_path=metadata, uncleaned_path=tmp_path / "vault-prices-1h.parquet", cleaned_path=cleaned, settlement_db_path=tmp_path / "unused-settlements.duckdb")
        frame = pd.read_parquet(cleaned)
        assert set(frame["address"]) == {d.address for d in ANTARCTIC_DEPLOYMENTS}
        regular = calculate_hourly_returns_for_all_vaults(frame)
        assert len(regular) == len(frame)
        monkeypatch.setattr(top_vaults_json, "native_datetime_utc_now", lambda: datetime.datetime(2026, 9, 17))  # noqa: DTZ001
        top_vaults_json.main(data_dir=tmp_path, vault_db_path=metadata, parquet_path=cleaned, output_path=tmp_path / "top_vaults_by_chain.json", core3_db_path=tmp_path / "unused-core3.duckdb", xerberus_db_path=tmp_path / "unused-xerberus.duckdb", feed_db_path=tmp_path / "unused-feeds.duckdb")
        saved = json.loads((tmp_path / "top_vaults_by_chain.json").read_text())
        top_vaults_json.validate_strict_json_serialisable(saved)
        vaults = {row["id"]: row for row in saved["vaults"]}
        assert {f"42161-{d.address}" for d in ANTARCTIC_DEPLOYMENTS}.issubset(vaults)
        for deployment in ANTARCTIC_DEPLOYMENTS:
            row = vaults[f"42161-{deployment.address}"]
            event = max((r for r in records if r.pool_address == deployment.address and r.kind == "AddLiquidity"), key=lambda r: r.block_number)
            assert row["protocol_slug"] == "antarctic"
            assert row["share_price_source"] == "smart-contract-event"
            assert row["deposit_manager"] is None
            assert row["last_updated_block"] == event.block_number
            assert row["last_updated_at"] == datetime.datetime.fromtimestamp(event.block_timestamp, datetime.UTC).replace(tzinfo=None).isoformat()
            assert row["last_share_price"] == pytest.approx(event.raw_usdt / event.raw_shares * 10**12)
            assert "Sparse handler-reported" in row["notes"]
        count = len(raw)
        write_antarctic_test_prices(web3, cache, tmp_path, start, end + 100)
        replay = pq.read_table(tmp_path / "vault-prices-1h.parquet").to_pandas()
        assert len(replay) == count
        pd.testing.assert_frame_equal(raw.drop(columns=["written_at"]), replay.drop(columns=["written_at"]))
    finally:
        cache.close()


def test_antarctic_one_observation_has_null_metrics(tmp_path: Path) -> None:
    """A single event remains one sample after the common daily preparation."""
    records = load_antarctic_settlements(FIXTURES / "antarctic-settlements.json")
    web3 = Web3(RecordedAntarcticProvider(FIXTURES / "antarctic-rpc.json"))
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    try:
        database = create_antarctic_test_metadata(web3, cache)
        event = max((r for r in records if r.pool_address == ANTARCTIC_DEPLOYMENTS[0].address and r.kind == "AddLiquidity"), key=lambda r: r.block_number)
        with AntarcticHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
            store.replace_range(event.pool_address, event.block_number, event.block_number + 1, iter([event]))
        write_antarctic_test_prices(web3, cache, tmp_path, event.block_number, event.block_number + 1)
        # Metadata enrichment normally performed by cleaning is needed here.
        raw = pd.read_parquet(tmp_path / "vault-prices-1h.parquet").set_index("timestamp")
        raw["id"] = "42161-" + event.pool_address
        raw["event_count"] = 0
        daily = calculate_hourly_returns_for_all_vaults(raw)
        assert len(daily) == 1
        metrics = calculate_lifetime_metrics(daily, database)
        row = metrics.iloc[0]
        assert row["one_month_cagr"] is None
        assert row["three_months_volatility"] is None
        assert all(p.raw_samples == 1 and p.error_reason is not None for p in row["period_results"])
    finally:
        cache.close()


def test_antarctic_small_price_changes_and_exact_repeats(tmp_path: Path) -> None:
    """Keep sub-10-basis-point price changes and retain every source event.

    The common writer suppresses an exact repeated ratio, while the Antarctic
    zero threshold preserves smaller observed changes at their real timestamps.

    :param tmp_path: Isolated raw price and historical context paths.
    :return: None.
    """
    records = load_antarctic_settlements(FIXTURES / "antarctic-settlements.json")
    base = next(r for r in records if r.kind == "AddLiquidity")
    events = [replace(base, block_number=base.block_number + i, block_timestamp=base.block_timestamp + i, transaction_hash="0x" + f"{i:064x}", raw_usdt=10_000_000 + (1000 if i else 0), raw_shares=10**19) for i in range(3)]
    web3 = Web3(RecordedAntarcticProvider(FIXTURES / "antarctic-rpc.json"))
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    try:
        create_antarctic_test_metadata(web3, cache)
        with AntarcticHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
            store.replace_range(base.pool_address, base.block_number, base.block_number + 3, iter(events))
        write_antarctic_test_prices(web3, cache, tmp_path, base.block_number, base.block_number + 3)
        rows = pd.read_parquet(tmp_path / "vault-prices-1h.parquet")
        assert rows["share_price"].tolist() == pytest.approx([1, 1.0001])
        assert rows["block_number"].tolist() == [base.block_number, base.block_number + 1]
        with AntarcticHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
            assert len(list(store.iter_settlements(base.pool_address, base.block_number, base.block_number + 3))) == 3  # noqa: PLR2004
    finally:
        cache.close()


def test_antarctic_mixed_contextual_and_polled_readers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Combine a real Antarctic reader with a polled reader using the old threshold.

    The common coordinator exercises both branches in one batch. Recorded
    state observations replace only Multicall transport; the default polled
    reader still suppresses a one-basis-point change while Antarctic retains it.

    :param tmp_path: Isolated context and token cache.
    :param monkeypatch: Polled state transport and reader factory substitutions.
    :return: None.
    """
    records = load_antarctic_settlements(FIXTURES / "antarctic-settlements.json")
    event = next(r for r in records if r.kind == "AddLiquidity")
    web3 = Web3(RecordedAntarcticProvider(FIXTURES / "antarctic-rpc.json"))
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    other_deployment = next(d for d in ANTARCTIC_DEPLOYMENTS if d.address != event.pool_address)
    contextual = AntarcticVault(web3, VaultSpec(42161, event.pool_address), token_cache=cache)
    polled = AntarcticVault(web3, VaultSpec(42161, other_deployment.address), token_cache=cache)
    contextual.historical_context_path = tmp_path / "vault-historical-context.duckdb"

    class PolledReader(VaultHistoricalReader):
        def construct_multicalls(self) -> object:  # noqa: PLR6301
            return iter(())

        def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list) -> VaultHistoricalRead:
            return VaultHistoricalRead(vault=self.vault, block_number=block_number, timestamp=timestamp, share_price=call_results[0].price, total_assets=None, total_supply=None, performance_fee=None, management_fee=None, errors=None)

    def state_transport(**_kwargs: object) -> object:
        for i in range(2):
            block = event.block_number + i
            timestamp = datetime.datetime.fromtimestamp(event.block_timestamp + i, datetime.UTC).replace(tzinfo=None)
            call = SimpleNamespace(block_identifier=block, extra_data={"vault": polled.address})
            result = SimpleNamespace(call=call, block_identifier=block, price=Decimal(1) + Decimal(i) / 10000)
            yield SimpleNamespace(block_number=block, timestamp=timestamp, results=[result])

    monkeypatch.setattr(polled, "get_historical_reader", lambda _stateful=False, **_kwargs: PolledReader(polled))
    monkeypatch.setattr(historical, "read_multicall_historical", state_transport)
    try:
        create_antarctic_test_metadata(web3, cache)
        with AntarcticHistoricalContextStore(contextual.historical_context_path) as store:
            store.replace_range(event.pool_address, event.block_number, event.block_number + 2, iter(replace(event, block_number=event.block_number + i, block_timestamp=event.block_timestamp + i, transaction_hash="0x" + f"{i:064x}", raw_usdt=10_000_000 + 1000 * i, raw_shares=10**19) for i in range(2)))
        coordinator = historical.VaultHistoricalReadMulticaller(lambda: web3, supported_quote_tokens=None, max_workers=1, token_cache=cache)
        rows = list(coordinator.read_historical([contextual, polled], event.block_number, event.block_number + 2, 1, reader_func=state_transport))
        assert [r.share_price for r in rows if r.vault.address == contextual.address] == [Decimal(1), Decimal("1.0001")]
        assert [r.share_price for r in rows if r.vault.address == polled.address] == [Decimal(1)]
    finally:
        cache.close()

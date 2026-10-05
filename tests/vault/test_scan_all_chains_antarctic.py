"""Antarctic support through the production all-chain Arbitrum scheduler."""

import datetime
import json
import pickle  # noqa: S403
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.antarctic import historical_context
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import AntarcticHistoricalContextStore
from eth_defi.testing.antarctic import RecordedAntarcticProvider, RecordedAntarcticStream, create_antarctic_test_metadata, load_antarctic_settlements
from eth_defi.token import TokenDiskCache
from eth_defi.vault import scan_all_chains, top_vaults_json
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.post_processing import clean_prices

FIXTURES = Path(__file__).parents[1] / "erc_4626/vault_protocol/fixtures"


def test_antarctic_all_chain_tick_prices_and_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0914
    """The real EVM coordinator prefills, writes and publishes both LP identities."""
    records = load_antarctic_settlements(FIXTURES / "antarctic-settlements.json")
    web3 = Web3(RecordedAntarcticProvider(FIXTURES / "antarctic-rpc.json"))
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    greylist = frozenset({HexAddress("0x" + "88" * 20)})
    atomic_writer = scan_all_chains.scan_historical_prices_to_parquet

    def publish_with_policy(**kwargs: object) -> dict:
        """Assert routine and late-repair calls share the injected policy."""
        assert kwargs["greylist"] == greylist
        return atomic_writer(**kwargs)

    monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", publish_with_policy)
    try:
        database = create_antarctic_test_metadata(web3, cache)
        database.write(tmp_path / "vault-metadata-db.pickle")
        # A warm chain's unrelated reader state must survive the first import.
        sentinel_spec = VaultSpec(42161, "0x" + "77" * 20)
        sentinel_state = {sentinel_spec: {"last_block": 510000000, "sentinel": "preserve"}}
        state_path = tmp_path / "reader-state.pickle"
        state_path.write_bytes(pickle.dumps(sentinel_state))
        state_before = state_path.read_bytes()

        async def open_stream(client: object, query: object) -> RecordedAntarcticStream:  # noqa: ARG001, RUF029
            return RecordedAntarcticStream(query, records)

        monkeypatch.setattr(historical_context, "open_hypersync_stream", open_stream)
        prefill = scan_all_chains.fetch_and_store_antarctic_history
        monkeypatch.setattr(scan_all_chains, "fetch_and_store_antarctic_history", lambda **kwargs: prefill(**kwargs, timestamp_cache_path=tmp_path / "timestamps"))
        monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *args, **kwargs: web3)  # noqa: ARG005
        monkeypatch.setattr(scan_all_chains, "MultiProviderWeb3Factory", lambda *args, **kwargs: lambda: web3)  # noqa: ARG005
        monkeypatch.setattr(scan_all_chains, "TokenDiskCache", lambda *args, **kwargs: cache)  # noqa: ARG005
        monkeypatch.setattr(scan_all_chains, "verify_rpc_provider_capabilities", lambda rpc, name: (rpc, 510301932))  # noqa: ARG005
        monkeypatch.setattr(scan_all_chains, "get_almost_latest_block_number", lambda connection: 510301932)  # noqa: ARG005
        monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *args, **kwargs: SimpleNamespace(hypersync_client=object()))  # noqa: ARG005
        monkeypatch.setenv("JSON_RPC_ARBITRUM", "https://recorded.invalid")
        monkeypatch.setattr(top_vaults_json, "native_datetime_utc_now", lambda: datetime.datetime(2026, 9, 17))  # noqa: DTZ001

        # Exercise real post-processing builders while omitting only external
        # enrichment and upload orchestration; separate tests cover live transport.
        def post_process(**kwargs: object) -> dict:
            assert clean_prices(vault_db_path=kwargs["vault_db_path"], uncleaned_path=kwargs["uncleaned_parquet_path"], cleaned_path=tmp_path / "cleaned-vault-prices-1h.parquet", settlement_db_path=tmp_path / "unused.duckdb")
            top_vaults_json.main(data_dir=tmp_path, vault_db_path=tmp_path / "vault-metadata-db.pickle", parquet_path=tmp_path / "cleaned-vault-prices-1h.parquet", output_path=tmp_path / "top_vaults_by_chain.json", core3_db_path=tmp_path / "unused-core3.duckdb", xerberus_db_path=tmp_path / "unused-xerberus.duckdb", feed_db_path=tmp_path / "unused-feed.duckdb")
            return {"clean-prices": True, "export-top-vaults-json": True}

        monkeypatch.setattr(scan_all_chains, "run_post_processing", post_process)
        options = dict(  # noqa: C408
            chains=[scan_all_chains.ChainConfig("Arbitrum", "JSON_RPC_ARBITRUM", False, greylist=greylist)],
            active_protocols=[],
            scan_prices=True,
            scan_hypercore=False,
            scan_grvt=False,
            scan_lighter=False,
            scan_hibachi=False,
            scan_apex=False,
            scan_core3=False,
            scan_currency_rates=False,
            max_workers=1,
            core3_max_workers=1,
            currency_api_max_workers=1,
            frequency="1h",
            retry_count=0,
            skip_post_processing=False,
            skip_cleaning=False,
            skip_top_vaults=False,
            skip_sparklines=True,
            skip_metadata=True,
            skip_data=True,
            skip_samples=True,
            vault_db_path=tmp_path / "vault-metadata-db.pickle",
            uncleaned_price_path=tmp_path / "vault-prices-1h.parquet",
            reader_state_path=state_path,
            hyperliquid_db_path=tmp_path / "absent-hyperliquid.duckdb",
            hyperliquid_hf_db_path=tmp_path / "absent-hf.duckdb",
            grvt_db_path=tmp_path / "absent-grvt.duckdb",
            lighter_db_path=tmp_path / "absent-lighter.duckdb",
            hibachi_db_path=tmp_path / "absent-hibachi.duckdb",
            apex_db_path=tmp_path / "absent-apex.duckdb",
            bkp_files=[],
            bkp_dir=tmp_path / "backups",
            historical_context_path=tmp_path / "vault-historical-context.duckdb",
            scan_vault_settlements=False,
            rpc_tracking_database_path=tmp_path / "rpc-tracking.duckdb",
        )
        results = scan_all_chains.run_scan_tick(**options)
        assert results["Arbitrum"].status == "success"
        assert results["Arbitrum"].price_rows > 0
        assert state_path.read_bytes() == state_before
        document = json.loads((tmp_path / "top_vaults_by_chain.json").read_text())
        assert {row["id"] for row in document["vaults"]} == {f"42161-{d.address}" for d in ANTARCTIC_DEPLOYMENTS}
        raw_before = pd.read_parquet(tmp_path / "vault-prices-1h.parquet")
        # Append an unrelated chain/address sentinel to the raw file, then run a
        # quiet cycle. No targeted repair may remove it or rewrite old history.
        sentinel = raw_before.iloc[[-1]].copy()
        sentinel["address"] = sentinel_spec.vault_address
        sentinel["share_price"] = 123.456
        pd.concat([raw_before, sentinel]).to_parquet(tmp_path / "vault-prices-1h.parquet", index=False)
        raw_bytes = (tmp_path / "vault-prices-1h.parquet").read_bytes()
        options["skip_post_processing"] = True
        quiet = scan_all_chains.run_scan_tick(**options)
        assert quiet["Arbitrum"].status == "success" and quiet["Arbitrum"].price_rows == 0
        assert (tmp_path / "vault-prices-1h.parquet").read_bytes() == raw_bytes
        assert state_path.read_bytes() == state_before
        # A late observation commits source context, but an atomic writer failure
        # must retain its repair marker and old prices for the next scan.
        newest = max((r for r in records if r.kind == "AddLiquidity"), key=lambda r: r.block_number)
        records.append(replace(newest, block_number=510301931, block_timestamp=1790759410, transaction_hash="0x" + "88" * 32, raw_usdt=newest.raw_usdt + 1))
        writer = scan_all_chains.scan_historical_prices_to_parquet

        def fail_writer(**_kwargs: object) -> None:
            message = "simulated atomic price writer failure"
            raise OSError(message)

        monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", fail_writer)
        retry_later = scan_all_chains.run_scan_tick(**options)
        assert retry_later["Arbitrum"].status == "failed"
        assert (tmp_path / "vault-prices-1h.parquet").read_bytes() == raw_bytes
        with AntarcticHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
            assert store.fetch_cursor(newest.pool_address)[1] == records[-1].block_number
        monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", writer)
        # Persistent failure backoff defers an ordinary tick; an operator can
        # explicitly retry the repaired writer without discarding source history.
        deferred = scan_all_chains.run_scan_tick(**options)
        assert deferred["Arbitrum"].status == "skipped"
        monkeypatch.setenv("FORCE_RPC_RETRY", "true")
        recovered = scan_all_chains.run_scan_tick(**options)
        monkeypatch.delenv("FORCE_RPC_RETRY")
        assert recovered["Arbitrum"].status == "success" and recovered["Arbitrum"].price_rows == 1
        with AntarcticHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
            assert store.fetch_cursor(newest.pool_address)[1] is None
        assert state_path.read_bytes() == state_before
        recovered_prices = pd.read_parquet(tmp_path / "vault-prices-1h.parquet")
        pd.testing.assert_frame_equal(recovered_prices[recovered_prices["address"] == sentinel_spec.vault_address].reset_index(drop=True), sentinel.reset_index(drop=True))
        raw_bytes = (tmp_path / "vault-prices-1h.parquet").read_bytes()
        # Missing source capability is a failure before destructive writing.
        monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *args, **kwargs: SimpleNamespace(hypersync_client=None))  # noqa: ARG005
        failed = scan_all_chains.run_scan_tick(**options)
        assert failed["Arbitrum"].status == "failed"
        assert (tmp_path / "vault-prices-1h.parquet").read_bytes() == raw_bytes
        assert state_path.read_bytes() == state_before
    finally:
        cache.close()

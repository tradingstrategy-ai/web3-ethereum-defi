"""Guarded real Hypersync reads and onchain Antarctic pipeline integration."""

import datetime
import json
import os
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import AntarcticHistoricalContextStore, fetch_and_store_antarctic_history
from eth_defi.erc_4626.vault_protocol.antarctic.vault import AntarcticVault
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.antarctic import create_antarctic_test_metadata, write_antarctic_test_prices
from eth_defi.token import TokenDiskCache
from eth_defi.vault import top_vaults_json
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.post_processing import clean_prices

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM or not HYPERSYNC_API_KEY, reason="JSON_RPC_ARBITRUM and HYPERSYNC_API_KEY required for real Antarctic integration")
LIVE_SETTLEMENTS = (
    (505781464, 1789567885, 101000000, 112710028839529595053, 6253637183332, "0x375492548824f0e2590f176e517f33f6848339b835c68eac57dbb3410353695c"),
    (503631188, 1789023857, 10000000, 9806185740351168406, 3182499724032, "0xfd8f513f15d1b60b0b64b58b72cb086b6708311e2a3081d8b3a5988483397e6c"),
)


@pytest.mark.parametrize("index", [0, 1])
def test_antarctic_real_hypersync_settlement(index: int, tmp_path: Path) -> None:
    """Read the reviewed settlement and replay a quiet range against real Hypersync."""
    web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)
    client = configure_hypersync_from_env(web3).hypersync_client
    assert client is not None
    deployment = ANTARCTIC_DEPLOYMENTS[index]
    block, timestamp, usdt, shares, tvl, transaction = LIVE_SETTLEMENTS[index]
    result = fetch_and_store_antarctic_history(web3=web3, hypersync_client=client, pool_start_blocks={deployment.address: block}, end_block=block + 1, context_path=tmp_path / "context.duckdb", timestamp_cache_path=tmp_path / "timestamps")
    assert result.observations_inserted == 1
    with AntarcticHistoricalContextStore(tmp_path / "context.duckdb") as store:
        observation = next(store.iter_settlements(deployment.address, block, block + 1))
        assert observation.block_timestamp == timestamp
        assert observation.transaction_hash == transaction
        assert (observation.raw_usdt, observation.raw_shares, observation.raw_tvl) == (usdt, shares, tvl)
        assert len(observation.block_hash) == 66  # noqa: PLR2004
    vault = AntarcticVault(web3, VaultSpec(42161, deployment.address))
    vault.historical_context_path = tmp_path / "context.duckdb"
    read = next(vault.get_historical_reader(False).fetch_contextual_historical_reads(block, block + 1, 1))
    assert read.share_price == Decimal(usdt) / Decimal(10**6) / (Decimal(shares) / Decimal(10**18))
    assert read.timestamp == datetime.datetime.fromtimestamp(timestamp, datetime.UTC).replace(tzinfo=None)
    replay = fetch_and_store_antarctic_history(web3=web3, hypersync_client=client, pool_start_blocks={deployment.address: block}, end_block=block + 1001, context_path=tmp_path / "context.duckdb", timestamp_cache_path=tmp_path / "timestamps")
    assert replay.observations_inserted == 0
    with AntarcticHistoricalContextStore(tmp_path / "context.duckdb") as store:
        assert store.fetch_cursor(deployment.address)[0] == block + 1001


def test_antarctic_live_events_to_prices_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real source transport reaches both Parquet files and the public JSON builder."""
    web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)
    client = configure_hypersync_from_env(web3).hypersync_client
    assert client is not None
    start, end = 470000000, 505781465
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    try:
        database = create_antarctic_test_metadata(web3, cache)
        database.write(tmp_path / "vault-metadata-db.pickle")
        result = fetch_and_store_antarctic_history(web3=web3, hypersync_client=client, pool_start_blocks={d.address: start for d in ANTARCTIC_DEPLOYMENTS}, end_block=end, context_path=tmp_path / "vault-historical-context.duckdb", timestamp_cache_path=tmp_path / "timestamps")
        assert result.observations_inserted > 2  # noqa: PLR2004
        with AntarcticHistoricalContextStore(tmp_path / "vault-historical-context.duckdb") as store:
            assert any(event.kind == "RemoveLiquidity" for d in ANTARCTIC_DEPLOYMENTS for event in store.iter_settlements(d.address, start, end))
        write_antarctic_test_prices(web3, cache, tmp_path, start, end)
        raw = pd.read_parquet(tmp_path / "vault-prices-1h.parquet")
        assert set(raw["address"]) == {d.address for d in ANTARCTIC_DEPLOYMENTS}
        assert raw["total_supply"].isna().all()
        assert clean_prices(vault_db_path=tmp_path / "vault-metadata-db.pickle", uncleaned_path=tmp_path / "vault-prices-1h.parquet", cleaned_path=tmp_path / "cleaned-vault-prices-1h.parquet", settlement_db_path=tmp_path / "unused.duckdb")
        monkeypatch.setattr(top_vaults_json, "native_datetime_utc_now", lambda: datetime.datetime(2026, 9, 17))  # noqa: DTZ001
        top_vaults_json.main(data_dir=tmp_path, vault_db_path=tmp_path / "vault-metadata-db.pickle", parquet_path=tmp_path / "cleaned-vault-prices-1h.parquet", output_path=tmp_path / "top_vaults_by_chain.json", core3_db_path=tmp_path / "unused-core3.duckdb", xerberus_db_path=tmp_path / "unused-xerberus.duckdb", feed_db_path=tmp_path / "unused-feeds.duckdb")
        saved = json.loads((tmp_path / "top_vaults_by_chain.json").read_text())
        vaults = {row["id"]: row for row in saved["vaults"]}
        for deployment, expected in zip(ANTARCTIC_DEPLOYMENTS, LIVE_SETTLEMENTS, strict=True):
            row = vaults[f"42161-{deployment.address}"]
            assert row["last_updated_block"] == expected[0]
            assert row["last_share_price"] == pytest.approx(expected[2] / expected[3] * 10**12)
            assert row["protocol_slug"] == "antarctic"
            assert row["share_price_source"] == "smart-contract-event"
        top_vaults_json.validate_strict_json_serialisable(saved)
    finally:
        cache.close()

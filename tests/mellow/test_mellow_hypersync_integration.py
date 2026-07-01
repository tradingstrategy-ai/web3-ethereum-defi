"""Integration tests for Mellow vault discovery and historical price rows."""

import os
from pathlib import Path

import flaky
import hypersync
import pandas as pd
import pytest

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.session import ThrottledHypersyncClient, create_throttled_hypersync_client
from eth_defi.mellow.vault import MellowVault
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultHistoricalRead
from eth_defi.vault.historical import scan_historical_prices_to_parquet

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

ETHEREUM_CHAIN_ID = 1
LIDO_EARN_USD_VAULT = "0x014e6DA8F283C4aF65B2AA0f201438680A004452"
LIDO_EARN_USD_VAULT_LOWER = LIDO_EARN_USD_VAULT.lower()
LIDO_EARN_USD_CREATED_BLOCK = 24_602_425
LIDO_EARN_USD_CREATED_AT = pd.Timestamp("2026-03-07 01:54:35")
DISCOVERY_START_BLOCK = 24_602_420
DISCOVERY_END_BLOCK = 24_602_430
PRICE_START_BLOCK = 24_999_754
PRICE_SECOND_BLOCK = 24_999_755
PRICE_EXCLUSIVE_END_BLOCK = 24_999_756
PRICE_STEP_BLOCKS = 1
EXPECTED_MAX_DETECTIONS = 10
EXPECTED_PRICE_ROWS = 2
EXPECTED_START_SHARE_PRICE = 1.0080417560461396
EXPECTED_END_SHARE_PRICE = 1.008280253418576

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None or HYPERSYNC_API_KEY is None,
    reason="JSON_RPC_ETHEREUM and HYPERSYNC_API_KEY needed to run these tests",
)


def _create_hypersync_client() -> ThrottledHypersyncClient:
    """Create an Ethereum Hypersync client.

    Keep stream concurrency at one request so this real-chain integration test
    does not burst against the shared Hypersync rate limit.
    """

    return create_throttled_hypersync_client(
        hypersync.ClientConfig(
            url=get_hypersync_server(ETHEREUM_CHAIN_ID),
            bearer_token=HYPERSYNC_API_KEY,
        ),
        concurrency=1,
    )


@flaky.flaky
def test_mellow_hypersync_discovery_and_historical_reader_price_row(tmp_path: Path) -> None:
    """Discover Lido Earn USD and write Mellow historical share-price rows.

    The discovery range is kept around the factory creation event. The price
    range then samples two neighbouring fixed blocks where
    ``Oracle.getReport(USDC)`` shows the share price increasing. The range is
    intentionally short because the historical pipeline fills a timestamp cache
    for every block in the requested range.
    """

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    web3factory = MultiProviderWeb3Factory(
        JSON_RPC_ETHEREUM,
        retries=2,
        skip_verification=True,
        expected_chain_id=ETHEREUM_CHAIN_ID,
    )
    hypersync_client = _create_hypersync_client()
    token_cache = TokenDiskCache(tmp_path / "tokens.sqlite")

    discover = HypersyncVaultDiscover(
        web3=web3,
        web3factory=web3factory,
        client=hypersync_client,
        max_workers=2,
        recv_timeout=60,
    )
    report = discover.scan_vaults(
        start_block=DISCOVERY_START_BLOCK,
        end_block=DISCOVERY_END_BLOCK,
        display_progress=False,
    )

    assert len(report.detections) <= EXPECTED_MAX_DETECTIONS
    detection = report.detections[LIDO_EARN_USD_VAULT_LOWER]
    assert detection.address == LIDO_EARN_USD_VAULT_LOWER
    assert detection.first_seen_at_block == LIDO_EARN_USD_CREATED_BLOCK
    assert detection.first_seen_at == LIDO_EARN_USD_CREATED_AT.to_pydatetime()
    assert detection.features == {ERC4626Feature.mellow_like}
    assert detection.deposit_count == 0
    assert detection.redeem_count == 0

    vault = create_vault_instance(
        web3,
        detection.address,
        features=detection.features,
        token_cache=token_cache,
    )
    assert isinstance(vault, MellowVault)
    vault.first_seen_at_block = detection.first_seen_at_block

    parquet_file = tmp_path / "mellow-prices.parquet"
    scan_result = scan_historical_prices_to_parquet(
        output_fname=parquet_file,
        web3=web3,
        web3factory=web3factory,
        vaults=[vault],
        token_cache=token_cache,
        start_block=PRICE_START_BLOCK,
        end_block=PRICE_EXCLUSIVE_END_BLOCK,
        step=PRICE_STEP_BLOCKS,
        max_workers=2,
        require_multicall_result=True,
        hypersync_client=hypersync_client,
        timestamp_cache_file=tmp_path / "timestamps",
        vault_addresses={LIDO_EARN_USD_VAULT_LOWER},
    )

    assert scan_result["rows_written"] == EXPECTED_PRICE_ROWS
    prices = pd.read_parquet(parquet_file)
    assert list(prices.columns) == VaultHistoricalRead.to_pyarrow_schema().names
    assert len(prices) == EXPECTED_PRICE_ROWS

    prices = prices.sort_values("block_number").reset_index(drop=True)
    assert prices["chain"].tolist() == [ETHEREUM_CHAIN_ID, ETHEREUM_CHAIN_ID]
    assert prices["address"].tolist() == [LIDO_EARN_USD_VAULT_LOWER, LIDO_EARN_USD_VAULT_LOWER]
    assert prices["block_number"].tolist() == [PRICE_START_BLOCK, PRICE_SECOND_BLOCK]
    assert prices["errors"].tolist() == ["", ""]
    assert prices["share_price"].notna().all()
    assert prices["total_assets"].notna().all()
    assert prices["total_supply"].notna().all()
    assert prices["written_at"].notna().all()

    start_row = prices.iloc[0]
    end_row = prices.iloc[1]
    assert start_row.share_price == pytest.approx(EXPECTED_START_SHARE_PRICE)
    assert end_row.share_price == pytest.approx(EXPECTED_END_SHARE_PRICE)
    assert end_row.share_price > start_row.share_price
    assert end_row.total_assets != start_row.total_assets
    assert end_row.total_supply != start_row.total_supply

    assert (prices["vault_poll_frequency"] == "first_read").all()
    assert (prices["deposits_open"] == "").all()
    assert (prices["redemption_open"] == "").all()
    assert (prices["trading"] == "").all()
    assert (prices["performance_fee"] == 0.0).all()
    assert (prices["management_fee"] == 0.0).all()
    assert prices["max_deposit"].isna().all()
    assert prices["max_redeem"].isna().all()
    assert prices["available_liquidity"].isna().all()
    assert prices["utilisation"].isna().all()

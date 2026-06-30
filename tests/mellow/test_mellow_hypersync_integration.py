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
PRICE_END_BLOCK = 24_602_445
PRICE_STEP_BLOCKS = 10
EXPECTED_MAX_DETECTIONS = 10
MELLOW_PRICE_UNSUPPORTED_ERROR = "Mellow share price and TVL require oracle report orientation and subvault accounting confirmation"

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None or HYPERSYNC_API_KEY is None,
    reason="JSON_RPC_ETHEREUM and HYPERSYNC_API_KEY needed to run these tests",
)


def _create_hypersync_client() -> hypersync.HypersyncClient:
    """Create an Ethereum Hypersync client."""

    return hypersync.HypersyncClient(
        hypersync.ClientConfig(
            url=get_hypersync_server(ETHEREUM_CHAIN_ID),
            bearer_token=HYPERSYNC_API_KEY,
        )
    )


@flaky.flaky
def test_mellow_hypersync_discovery_and_historical_reader_price_row(tmp_path: Path) -> None:
    """Discover Lido Earn USD and write the current Mellow historical row.

    This test intentionally asserts the current partial Mellow reader contract:
    ``total_supply`` is filled from ``ShareManager.totalSupply()``, while
    ``share_price`` and ``total_assets`` stay empty with an explicit error until
    Mellow oracle/NAV accounting is implemented.
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
        start_block=LIDO_EARN_USD_CREATED_BLOCK,
        end_block=PRICE_END_BLOCK,
        step=PRICE_STEP_BLOCKS,
        max_workers=2,
        require_multicall_result=True,
        hypersync_client=hypersync_client,
        timestamp_cache_file=tmp_path / "timestamps",
        vault_addresses={LIDO_EARN_USD_VAULT_LOWER},
    )

    assert scan_result["rows_written"] == 1
    prices = pd.read_parquet(parquet_file)
    assert list(prices.columns) == VaultHistoricalRead.to_pyarrow_schema().names
    assert len(prices) == 1

    row = prices.iloc[0]
    assert row.chain == ETHEREUM_CHAIN_ID
    assert row.address == LIDO_EARN_USD_VAULT_LOWER
    assert row.block_number == LIDO_EARN_USD_CREATED_BLOCK
    assert row.timestamp == LIDO_EARN_USD_CREATED_AT
    assert row.total_supply == 0.0
    assert row.errors == MELLOW_PRICE_UNSUPPORTED_ERROR
    assert row.vault_poll_frequency == "first_read"
    assert row.deposits_open == ""
    assert row.redemption_open == ""
    assert row.trading == ""
    assert pd.notna(row.written_at)

    assert pd.isna(row.share_price)
    assert pd.isna(row.total_assets)
    assert pd.isna(row.performance_fee)
    assert pd.isna(row.management_fee)
    assert pd.isna(row.max_deposit)
    assert pd.isna(row.max_redeem)
    assert pd.isna(row.available_liquidity)
    assert pd.isna(row.utilisation)

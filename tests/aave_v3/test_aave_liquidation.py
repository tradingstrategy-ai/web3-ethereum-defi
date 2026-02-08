from decimal import Decimal

import flaky
import pandas as pd
from web3 import Web3
import pytest
import os


import hypersync

from eth_defi.aave_v3.liquidation import AaveLiquidationReader
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

pytestmark = pytest.mark.skipif(
    (JSON_RPC_ETHEREUM is None) or (HYPERSYNC_API_KEY is None),
    reason="Set JSON_RPC_ETHEREUM and HYPERSYNC_API_KEY environment variable to Ethereum mainnet node to run this test",
)


@pytest.fixture()
def web3() -> Web3:
    return create_multi_provider_web3(JSON_RPC_ETHEREUM)


@pytest.fixture()
def hypersync_client() -> hypersync.HypersyncClient:
    hypersync_url = get_hypersync_server(1)  # Mainnet
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url, bearer_token=HYPERSYNC_API_KEY))
    return client


@flaky.flaky
def test_aave_liquidation_data(
    web3: Web3,
    hypersync_client: hypersync.HypersyncClient,
):
    """Read Aave liquidation events using HyperSync.

    - Take a snapshot of liquidation events on Ethereum mainnet and create a DataFrame out of it
    """

    reader = AaveLiquidationReader(
        client=hypersync_client,
        web3=web3,
    )

    # Before Black Friday liquidations
    events = reader.fetch_liquidations(start_block=23_000_000, end_block=23_100_000)
    assert len(events) == 256
    evt = events[0]

    # Test the liquidation event fields
    assert evt.chain_id == 1
    assert evt.chain_name == "Ethereum"
    assert evt.contract == "0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9"
    assert evt.block_number == 23000011
    assert evt.block_hash == "0x180a3b9cc52f25f9e9a8bdac84aff0bd5795393d7b91fc8bf555a6e33926f740"
    assert evt.timestamp == pd.Timestamp("2025-07-26 01:24:23")
    assert evt.transaction_hash == "0xe388c8cf1d5c6023e293a62112d3fc390a18e83e3e6b516f2521d9837867734e"
    assert evt.log_index == 676

    # Test collateral asset
    assert evt.collateral_asset.address == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    assert evt.collateral_asset.symbol == "USDC"
    assert evt.collateral_asset.decimals == 6
    assert evt.collateral_asset.chain_id == 1

    # Test debt asset
    assert evt.debt_asset.address == "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    assert evt.debt_asset.symbol == "WETH"
    assert evt.debt_asset.decimals == 18
    assert evt.debt_asset.chain_id == 1

    # Test liquidation details
    assert evt.user == "0xeCcB1786F292641B1AcA112964cf49f403b53809"
    assert evt.debt_to_cover == pytest.approx(Decimal("0.004152902240584987"))
    assert evt.liquidated_collateral_amount == pytest.approx(Decimal("16.157023"))
    assert evt.liquidator == "0x47d1515be2205c8c3ac9bc0ea740aba7660b7337"
    assert evt.receive_a_token is False

    df = pd.DataFrame([e.as_row() for e in events])
    assert len(df) == 256
    row = df.iloc[0]
    assert row["chain_id"] == 1
    assert row["chain_name"] == "Ethereum"
    assert row["debt_asset"] == "WETH"
    assert row["collateral_asset"] == "USDC"
    assert row["contract"] == "0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9"

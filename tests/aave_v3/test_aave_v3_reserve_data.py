"""Tests for reading reserve data."""
import json
import os

import pytest
import requests
from web3 import Web3, HTTPProvider

from eth_defi.aave_v3.reserve import HelperContracts, get_helper_contracts, fetch_reserves, fetch_reserve_data
from eth_defi.aave_v3.reserve import fetch_aave_reserves_snapshot
from eth_defi.chain import install_chain_middleware, install_retry_middleware


JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")
pytestmark = pytest.mark.skipif(not JSON_RPC_POLYGON, reason="This test needs Polygon node via JSON_RPC_POLYGON")


@pytest.fixture(scope="module")
def web3():
    """Live Polygon web3 instance."""
    web3 = Web3(HTTPProvider(JSON_RPC_POLYGON, session=requests.Session()))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    install_retry_middleware(web3)
    return web3


@pytest.fixture(scope="module")
def helpers(web3) -> HelperContracts:
    return get_helper_contracts(web3)


def test_aave_v3_fetch_reserve_list(
    web3: Web3,
    helpers: HelperContracts,
):
    """Get the list of reserve assets."""
    reserves = fetch_reserves(helpers)
    assert "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174" in reserves


def test_aave_v3_fetch_reserve_data(
    web3: Web3,
    helpers: HelperContracts,
):
    """Get the reserve data."""

    aggregated_reserve_data, base_currency_info = fetch_reserve_data(helpers)
    assert aggregated_reserve_data[0]["underlyingAsset"] == "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063"
    assert aggregated_reserve_data[0]["symbol"] == "DAI"

    one_usd = base_currency_info["marketReferenceCurrencyUnit"]
    assert one_usd == 100000000  # ChainLink units are used, so one USD multiplier has 8 decimal places


def test_aave_v3_fetch_reserve_snapshot(
    web3: Web3,
):
    """Get the reserve data snapshot."""

    snapshot = fetch_aave_reserves_snapshot(web3)
    assert snapshot["chain_id"] == 137
    assert snapshot["timestamp"] > 0
    assert snapshot["block_number"] > 0
    assert snapshot["reserves"]["0x8f3cf7ad23cd3cadbd9735aff958023239c6a063"]["symbol"] == "DAI"

    serialised = json.dumps(snapshot)
    unserialised = json.loads(serialised)
    assert unserialised == snapshot

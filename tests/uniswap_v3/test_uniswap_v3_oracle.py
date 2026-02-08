"""Price oracle testing for Uniswap V3.

To run tests in this module:

.. code-block:: shell

    export ETHEREUM_JSON_RPC="..."
    pytest -k test_eth_usdc_price_concurrent

"""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.price_oracle.oracle import PriceOracle, time_weighted_average_price
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.uniswap_v3.oracle import (
    update_price_oracle_concurrent,
    update_price_oracle_single_thread,
)
from eth_defi.uniswap_v3.pool import fetch_pool_details


@pytest.fixture
def web3_factory() -> TunedWeb3Factory:
    """Set up a Web3 connection generation factory"""
    return TunedWeb3Factory(os.environ["ETHEREUM_JSON_RPC"])


@pytest.fixture
def web3() -> Web3:
    """Set up a Web3 connection that supports multi-provider URLs"""
    return create_multi_provider_web3(os.environ["ETHEREUM_JSON_RPC"])


@pytest.fixture
def usdc_eth_address():
    """USDC/ETH 0.05% pool
    https://info.uniswap.org/#/pools/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640
    """
    return "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"


@flaky.flaky
@pytest.mark.skipif(
    os.environ.get("ETHEREUM_JSON_RPC") is None,
    reason="Set ETHEREUM_JSON_RPC environment variable to Ethereum node URL to run this test",
)
def test_eth_usdc_price_single_thread(web3, usdc_eth_address):
    """Calculate historical ETH price from Uniswap V3 pool."""

    # Randomly chosen block range
    start_block = 14_000_000
    end_block = 14_000_100

    pool_details = fetch_pool_details(web3, usdc_eth_address)
    assert pool_details.token0.symbol == "USDC"
    assert pool_details.token1.symbol == "WETH"

    oracle = PriceOracle(
        time_weighted_average_price,
        max_age=PriceOracle.ANY_AGE,  # We are dealing with historical data
        min_duration=datetime.timedelta(minutes=1),
    )

    update_price_oracle_single_thread(
        oracle,
        web3,
        usdc_eth_address,
        start_block,
        end_block,
        reverse_token_order=True,  # we want the price of ETH
    )

    oldest = oracle.get_oldest()
    assert oldest.block_number == 14_000_000
    assert oldest.timestamp == datetime.datetime(2022, 1, 13, 22, 59, 55)
    assert oldest.price == pytest.approx(Decimal("3250.2861765942502643156"))
    assert oldest.volume == pytest.approx(3.075302542833839)

    newest = oracle.get_newest()
    assert newest.block_number == 14_000_097
    assert newest.timestamp == datetime.datetime(2022, 1, 13, 23, 24, 40)
    assert newest.price == pytest.approx(Decimal("3259.0733672883275175991"))
    assert newest.volume == pytest.approx(1.5725140754556917)

    # We have 78 swaps for the duration
    assert len(oracle.buffer) == 78
    assert oracle.get_buffer_duration() == datetime.timedelta(seconds=1485)

    # TWAP
    assert oracle.calculate_price() == pytest.approx(Decimal("3253.806086408162965922"))


@flaky.flaky
@pytest.mark.skipif(
    os.environ.get("ETHEREUM_JSON_RPC") is None,
    reason="Set ETHEREUM_JSON_RPC environment variable to Ethereum node to run this test",
)
def test_eth_usdc_price_concurrent(web3, usdc_eth_address):
    """Calculate historical ETH price from Uniswap V3 pool."""

    # Randomly chosen block range
    start_block = 14_000_000
    end_block = 14_000_100

    pool_details = fetch_pool_details(web3, usdc_eth_address)
    assert pool_details.token0.symbol == "USDC"
    assert pool_details.token1.symbol == "WETH"

    oracle = PriceOracle(
        time_weighted_average_price,
        max_age=PriceOracle.ANY_AGE,  # We are dealing with historical data
        min_duration=datetime.timedelta(minutes=1),
    )

    update_price_oracle_concurrent(
        oracle,
        os.environ["ETHEREUM_JSON_RPC"],
        usdc_eth_address,
        start_block,
        end_block,
        reverse_token_order=True,  # we want the price of ETH
    )

    oldest = oracle.get_oldest()
    assert oldest.block_number == 14_000_000
    assert oldest.timestamp == datetime.datetime(2022, 1, 13, 22, 59, 55)
    assert oldest.price == pytest.approx(Decimal("3250.2861765942502643156"))
    assert oldest.volume == pytest.approx(3.075302542833839)

    newest = oracle.get_newest()
    assert newest.block_number == 14_000_097
    assert newest.timestamp == datetime.datetime(2022, 1, 13, 23, 24, 40)
    assert newest.price == pytest.approx(Decimal("3259.0733672883275175991"))
    assert newest.volume == pytest.approx(1.5725140754556917)

    # # We have 78 swaps for the duration
    assert len(oracle.buffer) == 78
    assert oracle.get_buffer_duration() == datetime.timedelta(seconds=1485)

    # TWAP
    assert oracle.calculate_price() == pytest.approx(Decimal("3253.806086408162965922"))

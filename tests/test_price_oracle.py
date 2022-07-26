"""Price oracle testing.

Tests are performed using BNB Chain mainnet fork and Ganache.

To run tests in this module:

.. code-block:: shell

    export BNB_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_price_oracle

"""
import datetime
import os
from decimal import Decimal

import pytest
from web3 import HTTPProvider, Web3
from eth_defi.price_oracle.oracle import PriceOracle, time_weighted_average_price, NotEnoughData, DataTooOld


#pytestmark = pytest.mark.skipif(
#    os.environ.get("BNB_CHAIN_JSON_RPC") is None,
#    reason="Set BNB_CHAIN_JSON_RPC environment variable to Binance Smart Chain node to run this test",
#)


@pytest.fixture
def web3():
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/latest/web3.eth.account.html#read-a-private-key-from-an-environment-variable
    web3 = Web3(HTTPProvider(os.environ["BNB_CHAIN_JSON_RPC"]))
    return web3


def test_oracle_no_data():
    """Price oracle cannot give price if there is no data."""

    oracle = PriceOracle(time_weighted_average_price)
    with pytest.raises(NotEnoughData):
        oracle.calculate_price()


def test_oracle_simple():
    """Calculate price over manually entered data."""

    price_data = {
        datetime.datetime(2021, 1, 1): Decimal(100),
        datetime.datetime(2021, 1, 2): Decimal(150),
        datetime.datetime(2021, 1, 3): Decimal(120),
    }

    oracle = PriceOracle(
        time_weighted_average_price,
        min_entries=1,
        max_age=PriceOracle.ANY_AGE,
    )

    oracle.feed_simple_data(price_data)

    # Heap is sorted oldest event first
    # Heap is sorted oldest event first
    assert oracle.get_newest().timestamp == datetime.datetime(2021, 1, 3)
    assert oracle.get_oldest().timestamp == datetime.datetime(2021, 1, 1)

    price = oracle.calculate_price()
    assert price == pytest.approx(Decimal("123.3333333333333333333333333"))


def test_oracle_feed_data_reverse():
    """Oracle heap is sorted the same even if we feed data in the reverse order."""

    price_data = {
        datetime.datetime(2021, 1, 3): Decimal(100),
        datetime.datetime(2021, 1, 2): Decimal(150),
        datetime.datetime(2021, 1, 1): Decimal(120),
    }

    oracle = PriceOracle(
        time_weighted_average_price,
    )

    oracle.feed_simple_data(price_data)

    # Heap is sorted oldest event first
    assert oracle.get_newest().timestamp == datetime.datetime(2021, 1, 3)
    assert oracle.get_oldest().timestamp == datetime.datetime(2021, 1, 1)


def test_oracle_too_old():
    """Price data is stale for real time."""

    price_data = {
        datetime.datetime(2021, 1, 1): Decimal(100),
        datetime.datetime(2021, 1, 2): Decimal(150),
        datetime.datetime(2021, 1, 3): Decimal(120),
    }

    oracle = PriceOracle(
        time_weighted_average_price,
        min_entries=1,
        max_age=PriceOracle.ANY_AGE,
    )

    oracle.feed_simple_data(price_data)

    with pytest.raises(DataTooOld):
        oracle.calculate_price()

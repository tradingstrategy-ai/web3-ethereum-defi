"""Price oracle testing.

Tests are performed using BNB Chain mainnet fork and Ganache.

To run tests in this module:

.. code-block:: shell

    export JSON_RPC_BINANCE="https://bsc-dataseed.binance.org/"
    pytest -k test_price_oracle

"""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3
from eth_defi.compat import install_retry_middleware_compat

from eth_defi.compat import clear_middleware
from eth_defi.price_oracle.oracle import PriceOracle, time_weighted_average_price, NotEnoughData, DataTooOld, DataPeriodTooShort
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.uniswap_v2.oracle import update_price_oracle_with_sync_events_single_thread
from eth_defi.uniswap_v2.pair import fetch_pair_details


@pytest.fixture
def web3_factory() -> MultiProviderWeb3Factory:
    """Set up a Web3 connection generation factury"""
    # https://web3py.readthedocs.io/en/latest/web3.eth.account.html#read-a-private-key-from-an-environment-variable
    return MultiProviderWeb3Factory(os.environ["JSON_RPC_BINANCE"])


@pytest.fixture
def web3() -> Web3:
    """Set up a Web3 connection generation factory"""

    # https://web3py.readthedocs.io/en/latest/web3.eth.account.html#read-a-private-key-from-an-environment-variable
    web3 = create_multi_provider_web3(os.environ["JSON_RPC_BINANCE"])

    # MIGRATED: Clear middleware with v6/v7 compatibility
    clear_middleware(web3)

    from web3.middleware import ExtraDataToPOAMiddleware

    install_retry_middleware_compat(web3)
    web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    return web3


@pytest.fixture
def bnb_busd_address():
    """https://tradingstrategy.ai/trading-view/binance/pancakeswap-v2/bnb-busd"""
    return "0x58F876857a02D6762E0101bb5C46A8c1ED44Dc16"


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
        max_age=datetime.timedelta(days=1),
    )

    oracle.feed_simple_data(price_data)

    with pytest.raises(DataTooOld):
        oracle.calculate_price()


def test_too_narrow_time_window():
    """We have data only over very short, manipulable, time window."""

    # Data for one second
    price_data = {
        datetime.datetime(2021, 1, 1): Decimal(100),
        datetime.datetime(2021, 1, 1, 0, 0, 1): Decimal(150),
    }

    oracle = PriceOracle(
        time_weighted_average_price,
        min_entries=1,
        max_age=datetime.timedelta(days=1),
    )

    oracle.feed_simple_data(price_data)

    with pytest.raises(DataPeriodTooShort):
        oracle.calculate_price()


# @pytest.mark.skipif(
#     os.environ.get("JSON_RPC_BINANCE") is None,
#     reason="Set JSON_RPC_BINANCE environment variable to Binance Smart Chain node to run this test",
# )
# @flaky.flaky(max_runs=2)
@pytest.mark.skip(reason="Fails on CI: Error: server returned an error response: error code -32603: EVM error CreateContractSizeLimit")
def test_bnb_busd_price(web3, bnb_busd_address):
    """Calculate historical BNB price from PancakeSwap pool."""

    # Randomly chosen block range.
    # 100 blocks * 3 sec / block = ~300 seconds
    start_block = 14_000_000
    end_block = 14_000_100

    pair_details = fetch_pair_details(web3, bnb_busd_address)
    assert pair_details.token0.symbol == "WBNB"
    assert pair_details.token1.symbol == "BUSD"

    oracle = PriceOracle(
        time_weighted_average_price,
        max_age=PriceOracle.ANY_AGE,  # We are dealing with historical data
        min_duration=datetime.timedelta(minutes=1),
    )

    update_price_oracle_with_sync_events_single_thread(oracle, web3, bnb_busd_address, start_block, end_block)

    oldest = oracle.get_oldest()
    assert oldest.block_number == 14_000_000
    assert oldest.timestamp == datetime.datetime(2022, 1, 2, 1, 18, 40)
    assert oldest.price == pytest.approx(Decimal("523.9534812968516567053232758"))

    newest = oracle.get_newest()
    assert newest.block_number == 14_000_100
    assert newest.timestamp == datetime.datetime(2022, 1, 2, 1, 23, 40)
    assert newest.price == pytest.approx(Decimal("523.6407772080559357061798420"))

    # We have 556 swaps for the duration
    assert len(oracle.buffer) == 556
    assert oracle.get_buffer_duration() == datetime.timedelta(seconds=300)

    assert oracle.calculate_price() == pytest.approx(Decimal("523.8243566658033237353702655"))

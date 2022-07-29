"""Price oracle implementation for Uniswap v3 pools."""
import datetime
from dataclasses import dataclass
from decimal import Decimal

from requests.adapters import HTTPAdapter
from web3 import Web3

from eth_defi.abi import get_contract
from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.reader import Filter, read_events, read_events_concurrent
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor
from eth_defi.price_oracle.oracle import PriceEntry, PriceOracle, PriceSource
from eth_defi.uniswap_v3.events import decode_swap
from eth_defi.uniswap_v3.pool import PoolDetails, fetch_pool_details


@dataclass
class UniswapV3PriceOracleContext(LogContext):
    """Hold data about tokens in the pool"""

    pool: PoolDetails

    reverse_token_order: bool


def convert_swap_event_to_price_entry(log: dict) -> PriceEntry:
    """Create a price entry based on eth_getLogs result.

    Called by :py:func:`update_price_oracle_single_thread`.
    """

    context: UniswapV3PriceOracleContext = log["context"]

    # Check our JSON-RPC has not served us something bad
    assert log["address"] == context.pool.address.lower(), f"Got wrong source address for Swap event. Expected pool contract {context.pool.address}, got {log['address']}"

    swap_info: dict = decode_swap(log)

    price = context.pool.convert_price_to_human(
        swap_info["tick"],
        context.reverse_token_order,
    )

    if context.reverse_token_order:
        volume = abs(swap_info["amount1"]) / 10**context.pool.token1.decimals
    else:
        volume = abs(swap_info["amount0"]) / 10**context.pool.token0.decimals

    return PriceEntry(
        timestamp=datetime.datetime.utcfromtimestamp(log["timestamp"]),
        price=Decimal(price),
        volume=volume,
        block_number=swap_info["block_number"],
        source=PriceSource.uniswap_v3_like_pool,
        pool_contract_address=swap_info["pool_contract_address"],
        block_hash=log["blockHash"],
        tx_hash=swap_info["tx_hash"],
    )


def update_price_oracle_concurrent(
    oracle: PriceOracle,
    json_rpc_url: str,
    pool_contract_address: str,
    start_block: int,
    end_block: int,
    reverse_token_order: bool = False,
    max_workers: int = 16,
):
    """Feed price oracle data for a given block range using using a thread pool

    Example:

    .. code-block: python

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

        assert oracle.calculate_price() == pytest.approx(Decimal("3253.806086408162965922"))

    :param oracle:
        Price oracle to update

    :param json_rpc_url:
        JSON-RPC URL

    :param pool_contract_address:
        Pool contract address

    :param start_block:
        First block to include data for

    :param end_block:
        Last block to include data for (inclusive)

    :param reverse_token_order:
        If pair token0 is the quote token to calculate the price.

    :param max_workers:
        How many threads to allocate for JSON-RPC IO.
    """
    http_adapter = HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
    web3_factory = TunedWeb3Factory(json_rpc_url, http_adapter)
    web3 = web3_factory(None)
    pool_details = fetch_pool_details(web3, pool_contract_address)
    log_context = UniswapV3PriceOracleContext(pool_details, reverse_token_order)
    executor = create_thread_pool_executor(web3_factory, log_context, max_workers=max_workers)

    Pool = get_contract(web3, "uniswap_v3/UniswapV3Pool.json")

    signatures = Pool.events.Swap.build_filter().topics
    assert len(signatures) == 1

    event_filter = Filter(
        contract_address=pool_contract_address,
        bloom=None,
        topics={
            signatures[0]: Pool.events.Swap,
        },
    )

    # Feed oracle with event data from JSON-RPC node
    for log_result in read_events_concurrent(
        executor,
        start_block,
        end_block,
        events=[Pool.events.Swap],
        notify=None,
        chunk_size=10,
        filter=event_filter,
        context=log_context,
    ):
        entry = convert_swap_event_to_price_entry(log_result)
        oracle.add_price_entry_reorg_safe(entry)


def update_price_oracle_single_thread(
    oracle: PriceOracle,
    web3: Web3,
    pool_contract_address: str,
    start_block: int,
    end_block: int,
    reverse_token_order: bool = False,
):
    """Feed price oracle data for a given block range.

    A slow single threaded implementation - suitable for testing.

    Example:

    .. code-block: python

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

        assert oracle.calculate_price() == pytest.approx(Decimal("3253.806086408162965922"))

    :param oracle:
        Price oracle to update

    :param web3:
        Web3 connection we use to fetch event data from JSON-RPC node

    :param pool_contract_address:
        Pool contract address

    :param start_block:
        First block to include data for

    :param end_block:
        Last block to include data for (inclusive)

    :param reverse_token_order:
        If pair token0 is the quote token to calculate the price.
    """
    Pool = get_contract(web3, "uniswap_v3/UniswapV3Pool.json")

    signatures = Pool.events.Swap.build_filter().topics
    assert len(signatures) == 1

    event_filter = Filter(
        contract_address=pool_contract_address,
        bloom=None,
        topics={
            signatures[0]: Pool.events.Swap,
        },
    )

    pool_details = fetch_pool_details(web3, pool_contract_address)
    log_context = UniswapV3PriceOracleContext(pool_details, reverse_token_order)

    # Feed oracle with event data from JSON-RPC node
    for log_result in read_events(
        web3,
        start_block,
        end_block,
        events=[Pool.events.Swap],
        notify=None,
        chunk_size=100,
        filter=event_filter,
        context=log_context,
    ):
        entry = convert_swap_event_to_price_entry(log_result)
        oracle.add_price_entry(entry)

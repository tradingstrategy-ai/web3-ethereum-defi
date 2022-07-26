"""Price oracle implementation for Uniswap v2 pools."""
import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import futureproof
from web3 import Web3

from eth_defi.abi import get_contract
from eth_defi.event_reader.conversion import decode_data, convert_int256_bytes_to_int
from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.reader import read_events_concurrent, Filter, read_events
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.price_oracle.oracle import PriceOracle, PriceEntry, PriceSource
from eth_defi.uniswap_v2.pair import PairDetails, fetch_pair_details


@dataclass
class UniswapV2PriceOracleContext(LogContext):
    """Hold data about tokens in in the pool"""
    pair: PairDetails

    reverse_token_order: bool


def convert_sync_log_result_to_price_entry(log: dict) -> PriceEntry:
    """Create a price entry based on Sync eth_getLogs result.

    Called by :py:func:`update_price_oracle_with_sync_events_single_thread`.
    """

    context: UniswapV2PriceOracleContext = log["context"]

    # Check our JSON-RPC has not served us something bad
    assert log["address"] == context.pair.address.lower(), f"Got wrong source address for Sync event. Expected pair contract {context.pair.address}, got {log['address']}"

    # {'address': '0xa6db9e0061cfb22da5755621bb363cdfe06057da',
    # 'topics': ['0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1'],
    # 'data': '0x000000000000000000000000000000000000000001b298e4a3c23039f6762b3b00000000000000000000000000000000000000000000000fdf9c1b5944aaa9a2',
    # 'blockNumber': '0xd59f80', 'transactionHash': '0x45cac9ba3f7a3bc93efecfa56c1aadffb31b5099842070d054e7b6111f1ac9bc', 'transactionIndex': '0x1', 'blockHash': '0x83447948658a5ae2dd1295cce35a85b61623fb9611578bcca65c4c918cbe3985', 'logIndex': '0x3', 'removed': False, 'context': None, 'event': <class 'web3._utils.datatypes.Sync'>, 'timestamp': 1641086320}
    timestamp = datetime.datetime.utcfromtimestamp(log["timestamp"])

    # Chop data blob to byte32 entries
    data_entries = decode_data(log["data"])

    reserve0 = convert_int256_bytes_to_int(data_entries[0])
    reserve1 = convert_int256_bytes_to_int(data_entries[1])

    assert reserve0 > 0
    assert reserve1 > 0

    price = context.pair.convert_price_to_human(
        reserve0,
        reserve1,
        context.reverse_token_order,
    )

    return PriceEntry(
        timestamp=timestamp,
        price=price,
        volume=None,  # For volume you would also need to get matching Swap() event
        block_number=int(log["blockNumber"], 16),
        source=PriceSource.uniswap_v2_like_pool_sync_event,
        pair_contract_address=log["address"],
        block_hash=log["blockHash"],
        tx_hash=log["transactionHash"]
    )

#
# def update_price_oracle_with_sync_events(
#     oracle: PriceOracle,
#     executor: futureproof.ThreadPoolExecutor,
#     web3_factory: Web3Factory,
#     pair_contract_address: str,
#     start_block: int,
#     end_block: int,
#     thread_pool_executor: Optional[futureproof.ThreadPoolExecutor],
#     ):
#     """Feed price oracle data for a given block range.
#
#     - Uses optimised parallel reading thread pool implmentation
#
#     - Uses fast multithreaded pool for the event fetch
#     """
#
#     web3 = Web3Factory(None)
#
#     Pair = get_contract(web3, "UniswapV2Pair.json")
#
#     events = [
#         Pair.events.Sync
#     ]
#
#     signatures = Pair.events.Sync.build_filter().topics
#     assert len(signatures) == 1
#
#     filter = Filter(
#         contract_address=pair_contract_address,
#         bloom=None,
#         topics={
#             signatures[0]: Pair.events.Sync,
#         }
#     )
#
#     for log_result in read_events_concurrent(
#             web3_factory,
#             start_block,
#             end_block,
#             [Pair.events.Sync],
#             notify=None,
#             chunk_size=100,
#             filter=filter,
#             context=None,
#     ):
#         import ipdb ; ipdb.set_trace()


def update_price_oracle_with_sync_events_single_thread(
    oracle: PriceOracle,
    web3: Web3,
    pair_contract_address: str,
    start_block: int,
    end_block: int,
    reverse_token_order=False,
    ):
    """Feed price oracle data for a given block range.

    A slow single threaded implementation - suitable for testing.

    Example:

    .. code-block: python

        # Randomly chosen block range.
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

        update_price_oracle_with_sync_events_single_thread(
            oracle,
            web3,
            bnb_busd_address,
            start_block,
            end_block
        )

        assert oracle.calculate_price() == pytest.approx(Decimal('523.8243566658033237353702655'))

    :param oracle:
        Price oracle to update

    :param web3:
        Web3 connection we use to fetch Sync event data from JSON-RPC node

    :param start_block:
        First block to include data for

    :param end_block:
        Last block to include data for (inclusive)

    :param reverse_token_order:
        If pair token0 is the quote token to calculate the price.
    """

    assert pair_contract_address

    Pair = get_contract(web3, "UniswapV2Pair.json")

    signatures = Pair.events.Sync.build_filter().topics
    assert len(signatures) == 1

    filter = Filter(
        contract_address=pair_contract_address,
        bloom=None,
        topics={
            signatures[0]: Pair.events.Sync,
        }
    )

    pool_details = fetch_pair_details(web3, pair_contract_address)

    # Feed oracle with event data from JSON-RPC node
    for log_result in read_events(
            web3,
            start_block,
            end_block,
            [Pair.events.Sync],
            notify=None,
            chunk_size=100,
            filter=filter,
            context=UniswapV2PriceOracleContext(pool_details, reverse_token_order),
    ):
        entry = convert_sync_log_result_to_price_entry(log_result)
        oracle.add_price_entry(entry)


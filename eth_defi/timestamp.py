"""Block timestamp related utilities"""
import dataclasses
import datetime
from typing import TypedDict

import requests
from eth_typing import HexStr
from web3 import Web3
from web3.types import BlockIdentifier

from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int
from eth_defi.utils import to_unix_timestamp



@dataclasses.dataclass(frozen=True, slots=True)
class FindBlockReply:
    """For block number estimation by time"""
    hash: HexStr
    block_number: int
    block_timestamp: datetime.datetime
    searched_timestamp: datetime.datetime


def get_latest_block_timestamp(web3: Web3) -> datetime.datetime:
    """Get the latest block timestamp.

    .. warning::

        Do not use

        See :py:func:`eth_defi.provider.broken_provider.get_almost_latest_block_number`

    :return:
        Timezone naive UTC datetime
    """
    last_block = web3.eth.get_block("latest")
    ts_str = last_block["timestamp"]

    # Depending on middleware, response might be converted or not
    if type(ts_str) == str:
        ts = int(ts_str, 16)
    else:
        ts = ts_str

    return datetime.datetime.utcfromtimestamp(ts)


def get_block_timestamp(web3: Web3, block_identifier: BlockIdentifier) -> datetime.datetime:
    """Get a  block timestamp.

    Slow method. Use only for individual queries.

    By hand:

    curl $JSON_RPC_MANTLE \
        -X POST \
        -H "Content-Type: application/json" \
        --data '{"method":"eth_getBlockByNumber","params":["0x1",false],"id":1,"jsonrpc":"2.0"}'

    :return:
        Timezone naive UTC datetime
    """

    try:
        # assert type(block_identifier) == int, "Only supports numeric block lookup"

        # Pass RPC machinery that seems to be broken for Mantle
        method = "eth_getBlockByNumber"
        if type(block_identifier) == int:
            args = (hex(block_identifier), False)
        else:
            args = (block_identifier, False)

        response = web3.provider.make_request(method, args)  # type: ignore

        data = response["result"]
        ts_str = data["timestamp"]

        # Depending on middleware, response might be converted or not
        if type(ts_str) == str:
            ts = convert_jsonrpc_value_to_int(ts_str)
        else:
            ts = ts_str

        return datetime.datetime.utcfromtimestamp(ts)
    except Exception as e:
        raise RuntimeError(f"Failed to read timestamp for block {block_identifier}, chain: {web3.eth.chain_id}: {e}") from e


def estimate_block_number_for_timestamp_by_findblock(
    chain_id: int,
    timestamp: datetime.datetime,
) -> FindBlockReply:
    """Estimate block number for a given timestamp.

    - To convert timestamps to block numbers
    - Uses `FindBlock API <https://www.findblock.xyz/>`__, using ``block/before`` API
    - Gets the block that was finaliesd at the timestamp or before it.

    :param timestamp:
        Timestamp to estimate the block number for

    :return:
        Estimated block number
    """

    assert isinstance(timestamp, datetime.datetime), "timestamp must be a datetime object"
    assert isinstance(chain_id, int), "chain_id must be an integer"

    unix_time = to_unix_timestamp(timestamp)

    response = requests.get(f"https://api.findblock.xyz/v1/chain/{chain_id}/block/before/{unix_time}?inclusive=true")

    data = response.json()
    return FindBlockReply(
        block_number=int(data["number"]),
        hash=HexStr(data["hash"]),
        block_timestamp=datetime.datetime.utcfromtimestamp(data["timestamp"]),
        searched_timestamp=timestamp,
    )


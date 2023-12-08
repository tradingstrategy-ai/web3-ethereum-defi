"""Block timestamp related utilities"""
import datetime

from web3 import Web3
from web3.types import BlockIdentifier

from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int


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

    :return:
        Timezone naive UTC datetime
    """
    last_block = web3.eth.get_block(block_identifier)
    ts_str = last_block["timestamp"]

    # Depending on middleware, response might be converted or not
    if type(ts_str) == str:
        ts = convert_jsonrpc_value_to_int(ts_str)
    else:
        ts = ts_str

    return datetime.datetime.utcfromtimestamp(ts)

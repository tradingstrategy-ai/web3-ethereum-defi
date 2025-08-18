"""Quicknode proprietary RPC calls."""

import dataclasses
import datetime

from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.utils import to_unix_timestamp


# {'network': 'base-mainnet', 'blockNumber': 26606733, 'timestamp': 1740002813}
@dataclasses.dataclass
class QuickNodeEstimatedBlock:
    """Estimated block number and timestamp."""

    network: str
    block_number: int
    timestamp: int


def estimate_block_number_for_timestamp_by_quicknode(
    web3: Web3,
    timestamp: datetime.datetime,
) -> QuickNodeEstimatedBlock:
    """Estimate block number for a given timestamp.

    - Use `QuickNode API <https://marketplace.quicknode.com/add-on/block-timestamp-lookup>`__
    - Use proprietary ``qn_getBlockFromTimestamp`` method
    :param web3:
        Web3 connection. Must use QuickNode as a provider.

    :param timestamp:
        Timestamp to estimate the block number for

    :return:
        Estimated block number
    """

    assert isinstance(timestamp, datetime.datetime), "timestamp must be a datetime object"

    unix_time = to_unix_timestamp(timestamp)

    reply = web3.provider.make_request(
        "qn_getBlockFromTimestamp",
        [unix_time],
    )
    # {'id': 13, 'result': {'network': 'base-mainnet', 'blockNumber': 26606733, 'timestamp': 1740002813}, 'jsonrpc': '2.0'}

    result = reply["result"]

    return QuickNodeEstimatedBlock(
        network=result["network"],
        block_number=result["blockNumber"],
        timestamp=native_datetime_utc_fromtimestamp(result["timestamp"]),
    )

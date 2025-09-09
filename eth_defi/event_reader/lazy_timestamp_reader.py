"""Lazily load block timestamps and headers.

See :py:func:`extract_timestamps_json_rpc_lazy`
"""

import logging
from typing import Callable

from hexbytes import HexBytes

from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int
from eth_typing import HexStr
from web3 import Web3
from web3.types import BlockIdentifier

from eth_defi.provider.named import get_provider_name

logger = logging.getLogger(__name__)


class OutOfSpecifiedRangeRead(Exception):
    """We tried to read a block outside out original given range."""


class LazyTimestampContainer:
    """Dictionary-like object to get block timestamps on-demand.

    Lazily load any block timestamp over JSON-RPC API if we have not
    cached it yet.

    See :py:func:`extract_timestamps_json_rpc_lazy`.

    TODO: This is not using middleware and fails to retry any failed JSON-RPC requests.
    """

    def __init__(
        self,
        web3: Web3,
        start_block: int,
        end_block: int,
        callback: Callable = None,
    ):
        """

        :param web3:
            Connection

        :param start_block:
            Start block range, inclusive

        :param end_block:
            End block range, inclusive
        """
        self.web3 = web3
        self.start_block = start_block
        self.end_block = end_block
        assert start_block > 0
        assert end_block >= start_block
        self.cache_by_block_hash = {}
        self.cache_by_block_number = {}

        #: How many API requets we have made
        self.api_call_counter = 0

        self.callback = callback

    def update_block_hash(self, block_identifier: BlockIdentifier) -> int:
        """Internal function to get block timestamp from JSON-RPC and store it in the cache."""
        # Skip web3.py stack of slow result formatters

        # TODO: Later tune down log level when successfully run in the production
        logger.info("update_block_hash(%s)", block_identifier)

        if type(block_identifier) == int:
            assert block_identifier > 0
            result = self.web3.manager.request_blocking("eth_getBlockByNumber", (hex(block_identifier), False))
        else:
            if isinstance(block_identifier, HexBytes):
                block_identifier = block_identifier.hex()

            # Make sure there is always 0x prefix for hashes
            if not block_identifier.startswith("0x"):
                block_identifier = "0x" + block_identifier

            result = self.web3.manager.request_blocking("eth_getBlockByHash", (block_identifier, False))

        name = get_provider_name(self.web3)
        assert result is not None, f"Node provider is low quality and does not serve blocks: {name}, was asking for block {block_identifier}"

        self.api_call_counter += 1

        # Note to self: block_number = 0 for the genesis block on Anvil
        block_number = convert_jsonrpc_value_to_int(result["number"])
        hash = result["hash"]

        # Make sure we conform the spec
        if not (self.start_block <= block_number <= self.end_block):
            raise OutOfSpecifiedRangeRead(f"Read block number {block_number:,} {hash} out of bounds of range {self.start_block:,} - {self.end_block:,}")

        timestamp = convert_jsonrpc_value_to_int(result["timestamp"])
        self.cache_by_block_hash[hash] = timestamp
        self.cache_by_block_number[block_number] = timestamp

        if self.callback:
            self.callback(hash, block_number, timestamp)

        return timestamp

    def __getitem__(self, block_hash: HexStr | HexBytes | str):
        """Get a timestamp of a block hash - v6/v7 compatible."""
        assert not type(block_hash) == int, f"Use block hashes, block numbers not supported, passed {block_hash}"
        assert type(block_hash) == str or isinstance(block_hash, HexBytes), f"Got: {block_hash} {block_hash.__class__}"

        # Ensure consistent string format for cache keys
        if type(block_hash) != str:
            if hasattr(block_hash, "hex"):
                # HexBytes object
                block_hash = block_hash.hex()
            else:
                # Fallback for other types
                block_hash = str(block_hash)

        # Ensure lowercase hex for consistent cache keys
        if block_hash.startswith("0x"):
            block_hash = block_hash.lower()
        else:
            block_hash = "0x" + block_hash.lower()

        if block_hash not in self.cache_by_block_hash:
            self.update_block_hash(block_hash)

        return self.cache_by_block_hash[block_hash]


def extract_timestamps_json_rpc_lazy(
    web3: Web3,
    start_block: int,
    end_block: int,
    fetch_boundaries=True,
) -> LazyTimestampContainer:
    """Create a cache container that instead of reading block timestamps upfront for the given range, only calls JSON-RPC API when requested

    - Works on the cases where sparse event data is read over long block range
      Use slow JSON-RPC block headers call to get this information.

    - The reader is hash based. It is mainly meant to resolve `eth_getLogs` resulting block hashes to
      corresponding event timestamps.

    - This is a drop-in replacement for the dict returned by eager :py:func:`eth_defi.reader.extract_timestamps_json_rpc`

    Example:

    .. code-block:: python

        # Allocate timestamp reader for blocks 1...100
        timestamps = extract_timestamps_json_rpc_lazy(web3, 1, 100)

        # Get a hash of some block
        block_hash = web3.eth.get_block(5)["hash"]

        # Read timestamp for block 5
        unix_time = timestamps[block_hash]

    For more information see

    - :py:func:`eth_defi.reader.extract_timestamps_json_rpc`

    - :py:class:`eth_defi.reorganisation_monitor.ReorganisationMonitor`

    :return:
        Wrapper object for block hash based timestamp access.

    """
    container = LazyTimestampContainer(web3, start_block, end_block)
    if fetch_boundaries:
        container.update_block_hash(start_block)
        container.update_block_hash(end_block)
    return container


class TrackedLazyTimestampReader:
    """Track block header fetching across multiple chunks.

    Monitor expensive eth_getBlock JSON-RPC process via :py:method:`get_count`.
    """

    def __init__(self):
        self.count = 0

    def extract_timestamps_json_rpc_lazy(
        self,
        web3: Web3,
        start_block: int,
        end_block: int,
        fetch_boundaries=True,
    ):
        container = LazyTimestampContainer(web3, start_block, end_block, callback=self.on_block_data)
        if fetch_boundaries:
            container.update_block_hash(start_block)
            container.update_block_hash(end_block)
        return container

    def on_block_data(self, block_hash, block_number, timestamp):
        self.count += 1

    def get_count(self) -> int:
        return self.count

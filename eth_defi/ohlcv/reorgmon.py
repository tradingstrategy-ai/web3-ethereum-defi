"""Chain reorganisation handling during the real-time OHLCV candle production."""

import datetime
from abc import abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, Optional
import logging

from web3 import Web3

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BlockRecord:
    block_number: int
    block_hash: str
    timestamp: int


@dataclass(slots=True)
class ChainReorganisationResolution:
    last_block_number: int
    latest_good_block: Optional[int]


class ChainReorganisationDetected(Exception):
    block_number: int
    original_hash: str
    new_hash: str

    def __init__(self, block_number: int, original_hash: str, new_hash: str):
        self.block_number = block_number
        self.original_hash = original_hash
        self.new_hash = new_hash

        super(f"Block reorg detected at #{block_number:,}. Original hash: {original_hash}. New hash: {new_hash}")


class ReorganisationResolutionFailure(Exception):
    """Chould not figure out chain reorgs after mutliple attempt.

    Node in a bad state?
    """



class ReorganisationMonitor:
    """Watch blockchain for reorgs."""

    def __init__(self, check_depth=200, max_reorg_resolution_attempts=10):
        self.block_map: Dict[int, BlockRecord] = {}
        self.last_block = 0
        self.check_depth = check_depth
        self.max_cycle_tries = max_reorg_resolution_attempts

    def add_block(self, record: BlockRecord):
        """Add new block to header tracking.

        Blocks must be added in order.
        """
        block_number = record.block_number
        assert block_number not in self.block_map, f"Block already added: {block_number}"
        self.block_map[block_number] = record

        assert self.last_block == block_number - 1, f"Blocks must be added in order. Last: {self.last_block}, got: {record}"
        self.last_block = block_number

    def check_block_reorg(self, block_number: int, block_hash: str):
        original = self.block_map.get(block_number)

        if original != block_hash:
            raise ChainReorganisationDetected(block_number, original, block_hash)

    def truncate(self, latest_good_block: int):
        """Delete data after a block number because chain reorg happened."""
        assert self.last_block
        for block_to_delete in range(latest_good_block + 1, self.last_block):
            del self.block_map[block_to_delete]
        self.last_block = latest_good_block

    def figure_reorganisation_and_new_blocks(self):

        chain_last_block = self.get_last_block()
        check_start_at = self.last_block = self.check_depth

        for block in self.get_block_data(check_start_at, chain_last_block):
            self.check_block_reorg(block.block_number, block.block_hash)

            if block.block_number not in self.block_map:
                self.add_block(block)

    def get_block_timestamp(self, block_number: int) -> int:
        """Return UNIX UTC timestamp of a block."""
        return self.block_map[block_number].timestamp

    def update_chain(self) -> ChainReorganisationResolution:
        """

        :return:
            Last block
        """

        tries_left = self.max_cycle_tries
        max_purge = None

        while tries_left > 0:
            try:
                self.figure_reorganisation_and_new_blocks()
                return ChainReorganisationResolution(self.last_block, max_purge)
            except ChainReorganisationDetected as e:
                logger.info("Chain reorganisation detected: %s", e)

                latest_good_block = e.block_number - 1

                if max_purge:
                    max_purge = min(latest_good_block, max_purge)
                else:
                    max_purge = e.block_number

                self.truncate(latest_good_block)
                tries_left -= 1

        raise ReorganisationResolutionFailure(f"Gave up chain reorg resolution. Last block: {self.last_block}, attempts {self.max_cycle_tries}")

    @abstractmethod
    def get_block_data(self, start_block, end_block) -> Iterable[BlockRecord]:
        """Fetch block header data"""

    @abstractmethod
    def get_last_block(self) -> int:
        """Get last block number"""


class JSONRPCReorganisationMonitor(ReorganisationMonitor):
    """Watch blockchain for reorgs using eth_getBlockByNumber JSON-RPC API."""

    def __init__(self, web3: Web3, check_depth=200, max_reorg_resolution_attempts=10):
        super().__init__(check_depth=check_depth, max_reorg_resolution_attempts=max_reorg_resolution_attempts)
        self.web3 = web3

    def get_last_block(self):
        return self.web3.eth.block_number

    def get_block_data(self, start_block, end_block) -> Iterable[BlockRecord]:
        logger.debug("Extracting timestamps for logs %d - %d", start_block, end_block)
        web3 = self.web3

        # Collect block timestamps from the headers
        for block_num in range(start_block, end_block + 1):
            raw_result = web3.manager.request_blocking("eth_getBlockByNumber", (hex(block_num), False))
            data_block_number = raw_result["number"]
            block_hash = raw_result["hash"]
            assert type(data_block_number) == str, "Some automatic data conversion occured from JSON-RPC data. Make sure that you have cleared middleware onion for web3"
            assert int(raw_result["number"], 16) == block_num

            timestamp = int(raw_result["timestamp"], 16)

            yield BlockRecord(block_num, block_hash, timestamp)



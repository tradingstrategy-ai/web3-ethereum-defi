"""Chain reorganisation handling during the chain data reading.

All EMV based blockchains are subject to minor chain reorganisation,
when nodes have not yet reached consensus on the chain tip around the world.
"""

import time
from abc import abstractmethod
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, Tuple, Optional, Type, Callable
import logging

import pandas as pd
from tqdm import tqdm
from web3 import Web3

from eth_defi.event_reader.block_header import BlockHeader


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ChainReorganisationResolution:

    #: What we know is the chain tip on our node
    last_live_block: int

    #: What we know is the block for which we do not need to perform rollback
    latest_block_with_good_data: int

    #: Did we detect any reorgs in this chycle
    reorg_detected: bool


class ChainReorganisationDetected(Exception):
    block_number: int
    original_hash: str
    new_hash: str

    def __init__(self, block_number: int, original_hash: str, new_hash: str):
        self.block_number = block_number
        self.original_hash = original_hash
        self.new_hash = new_hash

        super().__init__(f"Block reorg detected at #{block_number:,}. Original hash: {original_hash}. New hash: {new_hash}")


class ReorganisationResolutionFailure(Exception):
    """Chould not figure out chain reorgs after mutliple attempt.

    Node in a bad state?
    """


class BlockNotAvailable(Exception):
    """Tried to ask timestamp data for a block that does not exist yet."""


class ReorganisationMonitor:
    """Watch blockchain for reorgs.

    - Maintain the state of the last read block

    - Check block headers for chain reorganisations

    - Also manages the service for block timestamp lookups
    """

    def __init__(self, check_depth=200, max_reorg_resolution_attempts=10, reorg_wait_seconds=5):
        self.block_map: Dict[int, BlockHeader] = {}
        self.last_block_read: int = 0
        self.check_depth = check_depth
        self.reorg_wait_seconds = reorg_wait_seconds
        self.max_cycle_tries = max_reorg_resolution_attempts

    def has_data(self) -> bool:
        """Do we have any data available yet."""
        return len(self.block_map) > 0

    def get_last_block_read(self):
        return self.last_block_read

    def load_initial_block_headers(self, block_count: int, tqdm: Optional[Type[tqdm]] = None, save_callable: Optional[Callable] = None) -> Tuple[int, int]:
        """Get the inital block buffer filled up.

        :param tqdm:
            To display a progress bar

        :param save_callable:
            Save after every block.

            Called after every block.

            TODO: Hack. Design a better interface.

        :return:
            The initial block range to start to work with
        """
        end_block = self.get_last_block_live()
        start_block = max(end_block - block_count, 1)

        if len(self.block_map) > 0:
            # We have some initial data from the last (aborted) run,
            # We always need to start from the last save because no gaps in data allowed
            oldest_saved_block = max(self.block_map.keys())
            start_block = oldest_saved_block + 1

        blocks = end_block - start_block

        if tqdm:
            progress_bar = tqdm(total=blocks, colour="green")
            progress_bar.set_description(f"Downloading block headers {start_block:,} - {end_block:,}")
        else:
            progress_bar = None

        last_saved_block = None
        for block in self.fetch_block_data(start_block, end_block):
            self.add_block(block)

            if save_callable:
                last_saved_block, _ = save_callable()
                if last_saved_block:
                    last_saved_block_str = f"{last_saved_block:,}" if last_saved_block else "-"
                    progress_bar.set_postfix({"Last saved block": last_saved_block_str}, refresh=False)

            if progress_bar:
                progress_bar.update(1)

        if progress_bar:
            progress_bar.close()

        return start_block, end_block

    def add_block(self, record: BlockHeader):
        """Add new block to header tracking.

        Blocks must be added in order.
        """

        assert isinstance(record, BlockHeader)

        block_number = record.block_number
        assert block_number not in self.block_map, f"Block already added: {block_number}"
        self.block_map[block_number] = record

        if self.last_block_read != 0:
            assert self.last_block_read == block_number - 1, f"Blocks must be added in order. Last block we have: {self.last_block_read}, the new record is: {record}"
        self.last_block_read = block_number

    def check_block_reorg(self, block_number: int, block_hash: str):
        """Check that newly read block matches our record.

        If we do not have record, ignore.
        """
        original_block = self.block_map.get(block_number)
        if original_block is not None:
            if original_block.block_hash != block_hash:
                raise ChainReorganisationDetected(block_number, original_block.block_hash, block_hash)

    def truncate(self, latest_good_block: int):
        """Delete data after a block number because chain reorg happened.

        :param latest_good_block:
            Delete all data starting after this block (exclusive)
        """
        assert self.last_block_read
        for block_to_delete in range(latest_good_block + 1, self.last_block_read + 1):
            del self.block_map[block_to_delete]
        self.last_block_read = latest_good_block

    def figure_reorganisation_and_new_blocks(self):
        """Compare the local block database against the live data from chain.

        Spot the differences in (block number, block header) tuples
        and determine a chain reorg.
        """
        chain_last_block = self.get_last_block_live()
        check_start_at = max(self.last_block_read - self.check_depth, 1)
        for block in self.fetch_block_data(check_start_at, chain_last_block):
            self.check_block_reorg(block.block_number, block.block_hash)
            if block.block_number not in self.block_map:
                self.add_block(block)

    def get_block_by_number(self, block_number: int) -> BlockHeader:
        return self.block_map.get(block_number)

    def get_block_timestamp(self, block_number: int) -> int:
        """Return UNIX UTC timestamp of a block."""

        if not self.block_map:
            raise BlockNotAvailable("We have no records of any blocks")

        if block_number not in self.block_map:
            last_recorded_block_num = max(self.block_map.keys())
            raise BlockNotAvailable(f"Block {block_number} has not data, the latest live block is {self.get_last_block_live()}, last recorded is {last_recorded_block_num}")

        return self.block_map[block_number].timestamp

    def get_block_timestamp_as_pandas(self, block_number: int) -> pd.Timestamp:
        """Return UNIX UTC timestamp of a block."""

        ts = self.get_block_timestamp(block_number)
        return pd.Timestamp.utcfromtimestamp(ts).tz_localize(None)

    def update_chain(self) -> ChainReorganisationResolution:
        """Attemp

        - Do several attempt to read data (as a fork can cause other forks can cause fork)

        - Give up after some time if we detect the chain to be in a doom loop

        :return:
            What we think about the chain state
        """

        tries_left = self.max_cycle_tries
        max_purge = self.get_last_block_read()
        reorg_detected = False
        while tries_left > 0:
            try:
                self.figure_reorganisation_and_new_blocks()
                return ChainReorganisationResolution(self.last_block_read, max_purge, reorg_detected=reorg_detected)
            except ChainReorganisationDetected as e:
                logger.info("Chain reorganisation detected: %s", e)

                latest_good_block = e.block_number - 1

                reorg_detected = True

                if max_purge:
                    max_purge = min(latest_good_block, max_purge)
                else:
                    max_purge = e.block_number

                self.truncate(latest_good_block)
                tries_left -= 1
                time.sleep(self.reorg_wait_seconds)

        raise ReorganisationResolutionFailure(f"Gave up chain reorg resolution. Last block: {self.last_block_read}, attempts {self.max_cycle_tries}")

    def to_pandas(self, partition_size: int) -> pd.DataFrame:
        """Convert the data to Pandas DataFrame format for storing.

        :param partition_size:
            To partition the outgoing data.
        """
        data = [asdict(h) for h in self.block_map.values()]
        return BlockHeader.to_pandas(data, partition_size)

    def restore(self, block_map: dict):
        """Restore the chain state from a saved data."""
        assert type(block_map) == dict, f"Got: {type(block_map)}"
        self.block_map = block_map
        self.last_block_read = max(block_map.keys())

    @abstractmethod
    def fetch_block_data(self, start_block, end_block) -> Iterable[BlockHeader]:
        """Read the new block headers.

        :param start_block:
            The first block where to read (inclusive)

        :param end_block:
            The block where to read (inclusive)
        """

    @abstractmethod
    def get_last_block_live(self) -> int:
        """Get last block number"""


class JSONRPCReorganisationMonitor(ReorganisationMonitor):
    """Watch blockchain for reorgs using eth_getBlockByNumber JSON-RPC API."""

    def __init__(self, web3: Web3, check_depth=200, max_reorg_resolution_attempts=10):
        super().__init__(check_depth=check_depth, max_reorg_resolution_attempts=max_reorg_resolution_attempts)
        self.web3 = web3

    def get_last_block_live(self):
        return self.web3.eth.block_number

    def fetch_block_data(self, start_block, end_block) -> Iterable[BlockHeader]:
        total = end_block - start_block
        logger.info(f"Fetching block headers and timestamps for logs {start_block:,} - {end_block:,}, total {total:,} blocks")
        web3 = self.web3

        # Collect block timestamps from the headers
        for block_num in range(start_block, end_block + 1):
            raw_result = web3.manager.request_blocking("eth_getBlockByNumber", (hex(block_num), False))
            data_block_number = raw_result["number"]
            block_hash = raw_result["hash"]

            if type(data_block_number) == str:
                # Real node
                assert int(raw_result["number"], 16) == block_num
                timestamp = int(raw_result["timestamp"], 16)
            else:
                # EthereumTester
                timestamp = raw_result["timestamp"]

            record = BlockHeader(block_num, block_hash, timestamp)
            logger.debug("Fetched block record: %s, total %d transactions", record, len(raw_result["transactions"]))
            yield record


class MockChainAndReorganisationMonitor(ReorganisationMonitor):
    """A dummy reorganisation monitor for unit testing.

    Simulate block production and chain reorgs by minor forks,
    like a real blockchain.
    """

    def __init__(self, block_number: int = 1, block_duration_seconds=1):
        super().__init__(reorg_wait_seconds=0)

        #: Next available block number
        self.simulated_block_number = block_number
        self.simulated_blocks = {}
        self.block_duration_seconds = block_duration_seconds

    def produce_blocks(self, block_count=1):
        """Populate the fake blocks in mock chain.

        These blocks will be "read" in py:meth:`figure_reorganisation_and_new_blocks`.
        """
        for x in range(block_count):
            num = self.simulated_block_number
            record = BlockHeader(num, hex(num), int(num * self.block_duration_seconds))
            self.simulated_blocks[self.simulated_block_number] = record
            self.simulated_block_number += 1

    def produce_fork(self, block_number: int, fork_marker="0x8888"):
        """Mock a fork int he chain."""
        self.simulated_blocks[block_number] = BlockHeader(block_number, fork_marker, block_number * self.block_duration_seconds)

    def get_last_block_live(self):
        return self.simulated_block_number - 1

    def fetch_block_data(self, start_block, end_block) -> Iterable[BlockHeader]:

        assert start_block > 0, "Cannot ask data for zero block"
        assert end_block <= self.get_last_block_live(), "Cannot ask data for blocks that are not produced yet"

        for i in range(start_block, end_block + 1):
            yield self.simulated_blocks[i]

    def load(self, block_map: dict):
        self.simulated_blocks = block_map
        self.simulated_block_number = max(self.simulated_blocks.keys()) + 1


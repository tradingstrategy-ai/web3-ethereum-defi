"""Chain reorganisation handling during the chain data reading.

All EMV based blockchains are subject to minor chain reorganisation,
when nodes have not yet reached consensus on the chain tip around the world.
"""

import time
from abc import abstractmethod, ABC
from dataclasses import dataclass, asdict, field
from typing import Dict, Iterable, Tuple, Optional, Type, Callable
import logging
from urllib.parse import urljoin

import pandas as pd
from hexbytes import HexBytes
from tqdm import tqdm
from web3 import Web3, HTTPProvider

from eth_defi.event_reader.block_header import BlockHeader, Timestamp


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ChainReorganisationResolution:

    #: What we know is the chain tip on our node
    last_live_block: int

    #: What we know is the block for which we do not need to perform rollback
    latest_block_with_good_data: int

    #: Did we detect any reorgs in this chycle
    reorg_detected: bool

    def __repr__(self):
        return f"<reorg:{self.reorg_detected} last_live_block: {self.last_live_block:,}, latest_block_with_good_data:{self.latest_block_with_good_data:,}>"


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


@dataclass()
class ReorganisationMonitor(ABC):
    """Watch blockchain for reorgs.

    Most EMV blockchains have several minor chain organisations per day,
    when your node switched from one chain tip to another, due to
    block propagation issues. Any application reading blockchain
    event data must be able to detect such reorganisations
    and purge incorrect data from their data feeds.

    - Abstract base class for different ways
      to support chain reorganisations

    - Maintain the state where our blockchain read cursor is,
      using :py:meth:`get_last_block_read`

    - Ingest and maintain the state of the last read blocks
      using :py:meth:`update_chain`

    - Check block headers for chain reorganisations when
      reading events from the chain using :py:meth:`check_block_reorg`

    - Manages the service for block timestamp lookups,
      :py:meth:`get_block_timestamp`

    - Save and load block header state to disk cache,
      because APIs are slow, using :py:meth:`load_pandas`
      and :py:meth:`to_pandas`

    """

    #: Internal buffer of our block data
    #:
    #: Block number -> Block header data
    block_map: Dict[int, BlockHeader] = field(default_factory=dict)

    #: Last block served by :py:meth:`update_chain` in the duty cycle
    last_block_read: int = 0

    #: How many blocks we replay from the blockchain to detect any chain organisations
    #:
    #: Done by :py:meth:`figure_reorganisation_and_new_blocks`.
    #: Adjust this for your EVM chain.
    check_depth: int = 20

    #: How many times we try to re-read data from the blockchain in the case of reorganisation.
    #:
    #: If our node constantly feeds us changing data give up.
    max_cycle_tries = 10

    #: How long we allow our node to catch up in the case there has been a change in the chain tip.
    #:
    #: If our node constantly feeds us changing data give up.
    reorg_wait_seconds = 5

    def has_data(self) -> bool:
        """Do we have any data available yet."""
        return len(self.block_map) > 0

    def get_last_block_read(self) -> int:
        """Get the number of the last block served by update_chain()."""
        return self.last_block_read

    def get_block_by_number(self, block_number: int) -> BlockHeader:
        """Get block header data for a specific block number from our memory buffer."""
        return self.block_map.get(block_number)

    def load_initial_block_headers(self, block_count: Optional[int] = None, start_block: Optional[int] = None, tqdm: Optional[Type[tqdm]] = None, save_callable: Optional[Callable] = None) -> Tuple[int, int]:
        """Get the initial block buffer filled up.

        You can call this during the application start up,
        or when you start the chain. This interface is designed
        to keep the application on hold until new blocks have been served.

        :param block_count:
            How many latest block to load

            Give `start_block` or `block_count`.

        :param start_block:
            What is the first block to read.

            Give `start_block` or `block_count`.

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

        if block_count:
            assert not start_block, "Give block_cout or start_block"
            start_block = max(end_block - block_count, 1)
        else:
            pass

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

    def check_block_reorg(self, block_number: int, block_hash: str) -> Optional[Timestamp]:
        """Check that newly read block matches our record.

        - Called during the event reader

        - Event reader gets the block number and hash with the event

        - We have initial `block_map` in memory, previously buffered in

        - We check if any of the blocks in the block map have different values
          on our event produces -> in this case we know there has been a chain reorganisation

        If we do not have records, ignore.

        :raise ChainReorganisationDetected:
            When any if the block data in our internal buffer
            does not match those provided by events.
        """
        original_block = self.block_map.get(block_number)
        if original_block is not None:
            if original_block.block_hash != block_hash:
                raise ChainReorganisationDetected(block_number, original_block.block_hash, block_hash)

            return original_block.timestamp

        return None

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
        """Update the internal memory buffer of block headers from the blockchain node.

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

    def to_pandas(self, partition_size: int = 0) -> pd.DataFrame:
        """Convert the data to Pandas DataFrame format for storing.

        :param partition_size:
            To partition the outgoing data.

            Set 0 to ignore.

        """
        data = [asdict(h) for h in self.block_map.values()]
        return BlockHeader.to_pandas(data, partition_size)

    def load_pandas(self, df: pd.DataFrame):
        """Load block header data from Pandas data frame.

        :param df:

            Pandas DataFrame exported with :py:meth:`to_pandas`.
        """
        block_map = BlockHeader.from_pandas(df)
        self.restore(block_map)

    def restore(self, block_map: dict):
        """Restore the chain state from a saved data.

        :param block_map:
            Block number -> Block header dictionary
        """
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
    """Watch blockchain for reorgs using eth_getBlockByNumber JSON-RPC API.

    - Use expensive eth_getBlockByNumber call to download
      block hash and timestamp from Ethereum compatible node
    """

    def __init__(self, web3: Web3, **kwargs):
        super().__init__(**kwargs)
        self.web3 = web3

    def get_last_block_live(self):
        return self.web3.eth.block_number

    def fetch_block_data(self, start_block, end_block) -> Iterable[BlockHeader]:
        total = end_block - start_block
        logger.debug(f"Fetching block headers and timestamps for logs {start_block:,} - {end_block:,}, total {total:,} blocks")
        web3 = self.web3

        # Collect block timestamps from the headers
        for block_num in range(start_block, end_block + 1):
            response_json = web3.manager._make_request("eth_getBlockByNumber", (hex(block_num), False))
            raw_result = response_json["result"]

            # Happens the chain tip and https://polygon-rpc.com/
            # - likely the request routed to different backend node
            if raw_result is None:
                logger.debug("Abnormally terminated at block %d, chain tip unstable?", block_num)
                break

            data_block_number = raw_result["number"]

            block_hash = raw_result["hash"]
            if isinstance(block_hash, HexBytes):
                # Web3.py middleware madness
                block_hash = block_hash.hex()

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


class GraphQLReorganisationMonitor(ReorganisationMonitor):
    """Watch blockchain for reorgs using GoEthereum /graphql API.

    - This is ~10x - 100x faster than using JSON-RPC API

    - See https://geth.ethereum.org/docs/interacting-with-geth/rpc/graphql
      for details
    """

    def __init__(self, graphql_url: Optional[str] = None, provider: Optional[HTTPProvider] = None, **kwargs):
        """

        :param graphql_url:
            Give this or existing HTTPProvider

        :param provider:
            Give this or graphql_url
        """
        super().__init__(**kwargs)

        if provider:
            assert not graphql_url
            graphql_url = urljoin(provider.endpoint_uri, "/graphql")

        logger.debug("Connecting to GraphQL endpoint %s", graphql_url)
        self.client = self._create_client(graphql_url)

    def _create_client(self, api_url):
        """Create GQL GraphQL client used in queries.

        We turn on schema verification,
        as GoEthereum seems to support this.
        """
        from gql import Client
        from gql.transport.requests import RequestsHTTPTransport

        transport = RequestsHTTPTransport(
            url=api_url,
            verify=True,
            retries=3,
        )
        # Create a GraphQL client using the defined transport
        client = Client(transport=transport, fetch_schema_from_transport=True)
        return client

    def get_last_block_live(self) -> int:
        """Get the chain tip using GraphQL.

        - See https://geth.ethereum.org/docs/interacting-with-geth/rpc/graphql
          for details
        """
        from gql import gql

        # '{ "query": "query { block { number } }" }'
        query = gql(
            f"""
            query {{ block {{ number }} }}
        """
        )
        result = self.client.execute(query)
        # {'block': {'number': 37634011}}
        return result["block"]["number"]

    def fetch_block_data(self, start_block, end_block) -> Iterable[BlockHeader]:
        total = end_block - start_block
        logger.debug(f"Fetching block headers and timestamps for logs {start_block:,} - {end_block:,}, total {total:,} blocks")
        from gql import gql

        query = gql(
            f"""
            query {{
                blocks( from: {start_block}, to: {end_block} ) {{
                    number,
                    hash, 
                    timestamp
                }}
            }}
        """
        )
        result = self.client.execute(query)

        for inp in result["blocks"]:
            number = inp["number"]
            hash = inp["hash"]
            timestamp = int(inp["timestamp"], 16)
            yield BlockHeader(block_number=number, block_hash=hash, timestamp=timestamp)


class MockChainAndReorganisationMonitor(ReorganisationMonitor):
    """A dummy reorganisation monitor for unit testing.

    - Simulate block production and chain reorgs by minor forks,
      like a real blockchain.

    - We get the explicit control to introduce simulated forks
    """

    def __init__(self, block_number: int = 1, block_duration_seconds=1, **kwargs):
        super().__init__(**kwargs)

        #: Next available block number
        self.simulated_block_number = block_number
        self.simulated_blocks = {}
        self.block_duration_seconds = block_duration_seconds

        # There is no external data, so we do not need to wait for anything
        self.reorg_wait_seconds = 0

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

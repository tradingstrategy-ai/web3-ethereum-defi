"""High performance Solidity event reader.

Also see:

- `Ethereum JSON-RPC API spec <https://playground.open-rpc.org/?schemaUrl=https://raw.githubusercontent.com/ethereum/execution-apis/assembled-spec/openrpc.json&uiSchema%5BappBar%5D%5Bui:splitView%5D=false&uiSchema%5BappBar%5D%5Bui:input%5D=false&uiSchema%5BappBar%5D%5Bui:examplesDropdown%5D=false>`_
"""

import logging
from dataclasses import dataclass
from typing import Iterable, List, Protocol, Dict, Optional, TypedDict, Callable

from eth_bloom import BloomFilter

from web3 import Web3
from web3.contract import ContractEvent

from eth_defi.event_reader.logresult import LogContext, LogResult


@dataclass
class Filter:
    """Internal filter used to match events."""

    #: Preconstructed topic hash -> Event mapping
    topics: Dict[str, ContractEvent]

    #: Bloom filter to match block headers
    bloom: BloomFilter


# For typing.Protocol see https://stackoverflow.com/questions/68472236/type-hint-for-callable-that-takes-kwargs
class ProgressUpdate(Protocol):
    """Informs any listener about the state of an event scan.

    Called before a new block is processed.

    Hook this up with `tqdm` for an interactive progress bar.
    """

    def __call__(self,
                 current_block: int,
                 start_block: int,
                 end_block: int,
                 chunk_size: int,
                 total_events: int,
                 last_timestamp: Optional[int],
                 context: LogContext,
                 ):
        """
        :param current_block:
            The block we are about to scan.
            After this scan, we have scanned `current_block + chunk_size` blocks

        :param start_block:
            The first block in our total scan range

        :param end_block:
            The last block in our total scan range

        :param chunk_size:
            What was the chunk size (can differ for the last scanned chunk)

        :param total_events:
            Total events picked up so far

        :param last_timestamp:
            UNIX timestamp of last picked up event (if any events picked up)

        :param context:
            Current context
        """


def extract_timestamps_json_rpc(
        web3: Web3,
        start_block: int,
        end_block: int,
) -> Dict[str, int]:
    """Get block timestamps from block headers.

    Use slow JSON-RPC block headers call to get this information.

    :return:
        block hash -> UNIX timestamp mapping
    """
    timestamps = {}

    logging.debug("Extracting timestamps for logs %d - %d", start_block, end_block)

    # Collect block timestamps from the headers
    for block_num in range(start_block, end_block):
        raw_result = web3.manager.request_blocking("eth_getBlockByNumber", (hex(block_num), False))
        assert int(raw_result["number"], 16) == block_num
        timestamps[raw_result["hash"]] = int(raw_result["timestamp"], 16)

    return timestamps


def extract_events(
        web3: Web3,
        start_block: int,
        end_block: int,
        filter: Filter,
        context: Optional[LogContext] = None,
        extract_timestamps: Optional[Callable]=extract_timestamps_json_rpc,
) -> Iterable[LogResult]:
    """Perform eth_getLogs call over a block range.

    :param start_block:
        First block to process (inclusive)

    :param end_block:
        Last block to process (inclusive)

    :param filter:
        Internal filter used to match logs

    :param extract_timestamps:
        Method to get the block timestamps

    :param context:
        Passed to the all generated logs

    :return:
        Iterable for the raw event data
    """

    topics = list(filter.topics.keys())

    # https://www.quicknode.com/docs/ethereum/eth_getLogs
    # https://docs.alchemy.com/alchemy/guides/eth_getlogs
    filter_params = {
        "topics": [topics],  # JSON-RPC has totally braindead API to say how to do OR event lookup
        "fromBlock": hex(start_block),
        "toBlock": hex(end_block),
    }

    logging.debug("Extracting logs %s", filter_params)

    logs = web3.manager.request_blocking("eth_getLogs", (filter_params,))

    if logs:

        if extract_timestamps is not None:
            timestamps = extract_timestamps(web3, start_block, end_block)

        for log in logs:
            block_hash = log["blockHash"]
            # Retrofit our information to the dict
            event_signature = log["topics"][0]
            log["context"] = context
            log["event"] = filter.topics[event_signature]
            log["timestamp"] = timestamps[block_hash] if extract_timestamps else None
            yield log


def read_events(
    web3: Web3,
    start_block: int,
    end_block: int,
    events: List[ContractEvent],
    notify: Optional[ProgressUpdate],
    chunk_size: int = 100,
    context: Optional[LogContext] = None,
    extract_timestamps: Optional[Callable]=extract_timestamps_json_rpc,
) -> Iterable[LogResult]:
    """Reads multiple events from the blockchain.

    Optimized to read multiple events fast.

    - Scans chains block by block

    - Returns events as a dict for optimal performance

    - Can resume scan

    - Supports interactive progress bar

    - Reads all the events matching signature - any filtering must be done
      by the reader

    See `scripts/read-uniswap-v2-pairs-and-swaps.py` for a full example.

    Example:

    .. code-block:: python

        # HTTP 1.1 keep-alive
        session = requests.Session()

        json_rpc_url = os.environ["JSON_RPC_URL"]
        web3 = Web3(HTTPProvider(json_rpc_url, session=session))

        # Enable faster ujson reads
        patch_web3(web3)

        web3.middleware_onion.clear()

        # Get contracts
        Factory = get_contract(web3, "UniswapV2Factory.json")

        events = [
            Factory.events.PairCreated, # https://etherscan.io/txs?ea=0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f&topic0=0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9
        ]

        token_cache = TokenCache()

        start_block = 10_000_835  # Uni deployed
        end_block = 10_009_000  # The first pair created before this block

        # Read through the blog ran
        out = []
        for log_result in read_events(
            web3,
            start_block,
            end_block,
            events,
            None,
            chunk_size=1000,
            context=token_cache,
            extract_timestamps=None,
        ):
            out.append(decode_pair_created(log_result))

    :param web3:
        Web3 instance

    :param events:
        List of Web3.py contract event classes to scan for

    :param notify:
        Optional callback to be called before starting to scan each chunk

    :param start_block:
        First block to process (inclusive)

    :param end_block:
        Last block to process (inclusive)

    :param extract_timestamps:
        Override for different block timestamp extraction methods

    :param chunk_size:
        How many blocks to scan in one eth_getLogs call

    :param context:
        Passed to the all generated logs
    """

    total_events = 0

    assert len(web3.middleware_onion) == 0, f"Must not have any Web3 middleware installed to slow down scan, has {web3.middleware_onion.middlewares}"

    # Construct our bloom filter
    bloom = BloomFilter()
    topics = {}

    for event in events:
        signatures = event.build_filter().topics

        for signature in signatures:
            topics[signature] = event
            # TODO: Confirm correct usage of bloom filter for topics
            bloom.add(bytes.fromhex(signature[2:]))

    filter = Filter(topics, bloom)
    last_timestamp = None

    for block_num in range(start_block, end_block + 1, chunk_size):

        # Ping our master
        if notify is not None:
            notify(block_num, start_block, end_block, chunk_size, total_events, last_timestamp, context)

        last_of_chunk = min(end_block, block_num + chunk_size)

        # Stream the events
        for event in extract_events(web3, block_num, last_of_chunk, filter, context, extract_timestamps):
            total_events += 1
            yield event

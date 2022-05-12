"""High performance Solidity event reader.

To read:

- `Ethereum JSON-RPC API spec <https://playground.open-rpc.org/?schemaUrl=https://raw.githubusercontent.com/ethereum/execution-apis/assembled-spec/openrpc.json&uiSchema%5BappBar%5D%5Bui:splitView%5D=false&uiSchema%5BappBar%5D%5Bui:input%5D=false&uiSchema%5BappBar%5D%5Bui:examplesDropdown%5D=false>`_
"""

import logging
from dataclasses import dataclass
from typing import Iterable, List, Protocol, Dict, Optional, TypedDict

from eth_bloom import BloomFilter

from web3 import Web3
from web3.contract import ContractEvent

from eth_defi.block_reader.logresult import LogContext, LogResult


@dataclass
class Filter:
    """Internal filter we use to get all events once."""

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
        extract_timestamps=extract_timestamps_json_rpc,
) -> Iterable[LogResult]:
    """Perform eth_getLogs call over a log range.

    :param start_block:
        First block to process (inclusive)

    :param end_block:
        Last block to process (inclusive)

    :param extract_timestamps:
        Method to get the block timestamps

    :param context:
        Passed to the all generated logs

    :return:
        Iterable for the raw event data
    """

    topics = list(filter.topics.keys())
    topics = topics[0:1]

    # https://www.quicknode.com/docs/ethereum/eth_getLogs
    # https://docs.alchemy.com/alchemy/guides/eth_getlogs
    filter_params = {
        "topics": topics,
        "fromBlock": hex(start_block),
        "toBlock": hex(end_block),
    }

    logging.debug("Extracting logs %s", filter_params)

    logs = web3.manager.request_blocking("eth_getLogs", (filter_params,))

    if logs:
        timestamps = extract_timestamps(web3, start_block, end_block)

        for log in logs:

            block_hash = log["blockHash"]

            # Retrofit our information to the dict
            event_signature = log["topics"][0]
            log["context"] = context
            log["event"] = filter.topics[event_signature]
            log["timestamp"] = timestamps[block_hash]
            yield log


def read_events(
    web3: Web3,
    start_block: int,
    end_block: int,
    events: List[ContractEvent],
    notify: ProgressUpdate,
    chunk_size: int = 100,
    context: Optional[LogContext] = None,
    extract_timestamps=extract_timestamps_json_rpc,
) -> Iterable[LogResult]:
    """Reads multiple events from the blockchain.

    Optimized to read multiple events fast.

    - Scans chains block by block

    - Returns events as a dict for optimal performance

    - Can resume scan

    - Supports interactive progress bar

    - Reads all the events matching signature - any filtering must be done
      by the reader

    :param start_block:
        First block to process (inclusive)

    :param end_block:
        Last block to process (inclusive)

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
        #abi = event._get_event_abi()
        #import ipdb ; ipdb.set_trace()
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

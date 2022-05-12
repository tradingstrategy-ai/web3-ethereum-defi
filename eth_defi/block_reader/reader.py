"""High performance Solidity event reader.

To read:

- `Ethereum JSON-RPC API spec <https://playground.open-rpc.org/?schemaUrl=https://raw.githubusercontent.com/ethereum/execution-apis/assembled-spec/openrpc.json&uiSchema%5BappBar%5D%5Bui:splitView%5D=false&uiSchema%5BappBar%5D%5Bui:input%5D=false&uiSchema%5BappBar%5D%5Bui:examplesDropdown%5D=false>`_
"""

import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Protocol, Dict

from eth_bloom import BloomFilter
from eth_typing import HexStr

from web3 import Web3
from web3._utils.events import construct_event_topic_set
from web3.contract import ContractEvent


@dataclass
class Filter:
    #: Preconstructed topic hash -> Event mapping
    topics: Dict[str, ContractEvent]

    #: Bloom filter to match block headers
    bloom: BloomFilter


# For typing.Protocol see https://stackoverflow.com/questions/68472236/type-hint-for-callable-that-takes-kwargs
class ProgressUpdate(Protocol):
    """Informs any listener about the state of the blockchain scan.

    Called before a new block is processed.

    Hook this up with `tqdm` for an interactive progress bar.
    """

    def __call__(current_block: int,
                 start_block: int,
                 end_block: int,
                 total_events: int):
        pass


def extract_timestamps_json_rpc(
        web3: Web3,
        start_block: int,
        end_block: int,
) -> Dict[int, int]:
    """Get block timestamps from block headers.

    Use slow JSON-RPC path.
    """
    timestamps = {}

    logging.debug("Extracting timestamps for logs %d - %d", start_block, end_block)

    # Collect block timestamps from the headers
    for block_num in range(start_block, end_block):
        raw_result = web3.manager.request_blocking("eth_getBlockByNumber", (hex(block_num), False))
        assert int(raw_result["number"], 16) == block_num
        timestamps[block_num] = int(raw_result["timestamp"], 16)

    return timestamps


def extract_events(
        web3: Web3,
        start_block: int,
        end_block: int,
        filter: Filter,
        extract_timestamps=extract_timestamps_json_rpc,
) -> Iterable[dict]:
    """Perform eth_getLogs call over a log range.

    :return:
        Iterable for the raw event data
    """
    timestamps = extract_timestamps(web3, start_block, end_block)
    topics = list(filter.topics.keys())
    logs = web3.manager.request_blocking("eth_getLogs", (start_block, end_block, None, topics))
    for log in logs:
        print(log)


def read_events(
    web3: Web3,
    start_block: int,
    end_block: int,
    events: List[ContractEvent],
    notify: ProgressUpdate,
    chunk_size: int = 100,
    extract_timestamps=extract_timestamps_json_rpc,
) -> Iterable[dict]:
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

    """

    total_events = 0

    assert len(web3.middleware_onion) == 0, f"Must not have any Web3 middleware installed to slow down, has {web3.middleware_onion.middlewares}"

    # Construct our bloom filter
    bloom = BloomFilter()
    topics = {}

    for event in events:
        abi = event._get_event_abi()
        signatures = construct_event_topic_set(abi, web3.codec)
        for signature in signatures:
            topics[signature] = event
            # TODO: Confirm correct usage of bloom filter for topics
            bloom.add(bytes.fromhex(signature[2:]))

    filter = Filter(topics, bloom)

    for block_num in range(start_block, end_block, chunk_size):

        # Ping our master
        if notify is not None:
            notify(block_num, start_block, end_block, total_events)

        # Stream the events
        for event in extract_events(web3, block_num, block_num + chunk_size, filter, extract_timestamps):
            total_events += 1
            yield event






import requests
import json
from enum import Enum

from eth_defi.event_reader.block_header import BlockHeader


class AnkrSupportedBlockchain(Enum):
    eth = "eth"
    bsc = "bsc"
    polygon = "polygon"
    fantom = "fantom"
    arbitrum = "arbitrum"
    avalanche = "avalanche"
    syscoin = "syscoin"


def make_block_request_ankr(endpoint_url: str, start_block: int | str = "latest", end_block: int | str = "latest", blockchain: AnkrSupportedBlockchain | None = None) -> list[dict]:
    """Fetch blocks from Ankr API
    
    :param endpoint_url: URL of Ankr API endpoint. Should be multichain endpoint.
    
    :param start_block: Block number to start fetching from. Can be an int or "latest".
    
    :param end_block: Block number to end fetching at. Can be an int or "latest".
    
    :param blockchain: Blockchain to fetch blocks from. Must be of type AnkrSupportedBlockchain.
    
    :return: List of blocks in JSON format.
    """
    if start_block == "latest":
        end_block = "latest"

    assert type(start_block) == int or start_block == "latest", "start_block must be an int or 'latest'"
    assert type(end_block) == int or end_block == "latest", "end_block must be an int or None"
    assert isinstance(blockchain, AnkrSupportedBlockchain), "blockchain must be of type AnkrSupportedBlockchain"

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
    }

    data = {
        "jsonrpc": "2.0",
        "method": "ankr_getBlocks",
        "params": {
            "blockchain": blockchain.value,
            "fromBlock": start_block,
            "toBlock": end_block,
            "includeTxs": False,
            "includeLogs": False,
        },
        "id": 10,
    }

    result = requests.post(endpoint_url, headers=headers, json=data)

    j = result.json()

    blocks = j["result"]["blocks"]

    return blocks


def extract_timestamps_ankr_get_block(
    endpoint_url: str,
    start_block: int | None = None,
    end_block: int | None = None,
    max_blocks_at_once: int = 30,
) -> list[int]:
    """Extract timestamps from Ankr API
    
    :param endpoint_url: URL of Ankr API endpoint. Should be multichain endpoint.
    
    :param start_block: Block number to start fetching from. Can be an int or None.
    
    :param end_block: Block number to end fetching at. Can be an int or None.
    
    :param max_blocks_at_once: Maximum number of blocks to fetch at once. Default is 30.

    :return: List of timestamps in int format.
    """
    timestamps = []

    for i in range(start_block, end_block + 1, max_blocks_at_once):
        blocks = make_block_request_ankr(endpoint_url, i, min(i + max_blocks_at_once - 1, end_block))
        timestamps.extend([int(x["timestamp"], 16) for x in blocks])

    return timestamps

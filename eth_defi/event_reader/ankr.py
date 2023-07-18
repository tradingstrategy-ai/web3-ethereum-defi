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


def make_block_request_ankr(endpoint_url: str, start_block: int | str = "latest", end_block: int | str = "latest", blockchain: AnkrSupportedBlockchain | None = None):
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
):
    timestamps = []

    for i in range(start_block, end_block + 1, max_blocks_at_once):
        blocks = make_block_request_ankr(endpoint_url, i, min(i + max_blocks_at_once - 1, end_block))
        timestamps.extend([int(x["timestamp"], 16) for x in blocks])

    return timestamps

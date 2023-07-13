"""Lazily load block timestamps and headers."""
from eth_defi.event_reader.conversion import convert_jsonrpc_value_to_int
from eth_typing import HexStr
from web3 import Web3
from web3.types import BlockIdentifier


class LazyTimestampContainer:
    """Dictionary-like object to get block timestamps on-demand.

    See :py:func:`extract_timestamps_json_rpc_lazy`.
    """

    def __init__(self, web3: Web3, start_block: int, end_block: int):
        self.web3 = web3
        self.start_block = start_block
        self.end_block = end_block
        self.cache_by_block_hash = {}

    def update_block_hash(self, block_identifier: BlockIdentifier) -> int:
        result = self.web3.eth.get_block(block_identifier)
        # data_block_number = convert_jsonrpc_value_to_int(result["number"])
        hash = result["hash"]
        timestamp = convert_jsonrpc_value_to_int(result["timestamp"])
        self.cache_by_block_hash[hash] = timestamp
        return timestamp

    def __getitem__(self, block_hash: HexStr | str):

        if type(block_hash) == str:
            block_hash = HexStr(block_hash)

        assert isinstance(block_hash, HexStr), "Use block hashes, block numbers not supported"
        if block_hash not in self.cache_by_block_hash:
            self.update_block_hash(block_hash)

        return self.cache_by_block_hash[block_hash]



def extract_timestamps_json_rpc_lazy(
    web3: Web3,
    start_block: int,
    end_block: int,
    fetch_boundaries=True,
) -> LazyTimestampContainer:
    """Get block timestamps from block headers.

    Use slow JSON-RPC block headers call to get this information.

    TODO: This is an old code path. This has been replaced by more robust
    :py:class:`ReorganisationMonitor` implementation.

    :return:
        block hash -> UNIX timestamp mapping
    """
    container = LazyTimestampContainer(web3, start_block, end_block)
    if fetch_boundaries:
        container.update_block_hash(start_block)
        container.update_block_hash(end_block)
    return container

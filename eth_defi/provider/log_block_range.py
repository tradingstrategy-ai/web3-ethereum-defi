"""How long log queries are split into smaller batches per RPC provider."""

from web3 import Web3


def get_logs_max_block_range(web3: Web3) -> int:
    """Get how many blocks is the max batch size in eth_getLogs for this RPC provider.

    - See https://www.alchemy.com/docs/node/ethereum/ethereum-api-endpoints/eth-get-logs
    """
    # Default to 10k blocks
    return 10_000
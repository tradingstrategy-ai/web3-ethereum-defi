"""How long log queries are split into smaller batches per RPC provider."""

from web3 import Web3

from eth_defi.provider.named import get_provider_name


def get_logs_max_block_range(web3: Web3) -> int:
    """Get how many blocks is the max batch size in eth_getLogs for this RPC provider.

    - See https://www.alchemy.com/docs/node/ethereum/ethereum-api-endpoints/eth-get-logs
    - Handle broken chains and subpar RPC providers
    """

    name = get_provider_name(web3.provider)
    if "tac" in name:
        # Assume public TAC RPC (no paid ones available yet)
        return 1_000

    # Default to 10k blocks
    return 10_000

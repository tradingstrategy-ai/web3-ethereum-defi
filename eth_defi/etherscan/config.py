"""Etherscan configurations."""

#: Etherscan link per chain name
ETHERSCAN_URLS = {
    1: "https://etherscan.io",  # Ethereum
    56: "https://bscscan.com",  # Binance Smart Chain (BSC)
    137: "https://polygonscan.com",  # Polygon
    43114: "https://snowscan.xyz",  # Avalanche C-Chain
    80094: None,  # Berachain - No official Etherscan-like explorer found; may use Blockscout
    130: "https://explorer.unichain.org",  # Unichain
    645749: None,  # Hyperliquid - No official explorer found; chain ID uncertain
    8453: "https://basescan.org",  # Base
    146: None,  # Sonic - No official Etherscan-like explorer found; may use Blockscout
    34443: "https://explorer.mode.network",  # Mode
    5000: "https://mantlescan.xyz",  # Mantle
    999: None,  # Hyperliquid - Duplicate/uncertain chain ID; no official explorer found
    42161: "https://arbiscan.io",  # Arbitrum One
    2741: None,  # Abstract - No official Etherscan-like explorer found; may use Blockscout
    10: "https://optimistic.etherscan.io",  # Optimism
    1868: None,  # Soneium - No official Etherscan-like explorer found
    324: "https://explorer.zksync.io",  # ZKsync Era
    100: "https://gnosisscan.io",  # Gnosis Chain
    81457: "https://blastscan.io",  # Blast
    42220: "https://celoscan.io",  # Celo
    7777777: "https://zora.superscan.network",  # Zora
    57073: None,  # Ink - No official Etherscan-like explorer found; may use Blockscout
}


def get_etherscan_url(chain_id: int) -> str | None:
    assert type(chain_id) is int, f"Chain ID must be an integer, got {type(chain_id)}"
    url = ETHERSCAN_URLS.get(chain_id)
    return url


def get_etherscan_tx_link(chain_id: int, tx_hash: str) -> str | None:
    """Get the Etherscan transaction link for a given chain ID and transaction hash."""
    url = get_etherscan_url(chain_id)
    assert url is not None, f"No Etherscan URL found for chain ID {chain_id}"
    return f"{url}/tx/{tx_hash}"


def get_etherscan_address_link(chain_id: int, address: str) -> str | None:
    """Get the Etherscan address link for a given chain ID and address."""
    url = get_etherscan_url(chain_id)
    assert url is not None, f"No Etherscan URL found for chain ID {chain_id}"
    return f"{url}/address/{address}"

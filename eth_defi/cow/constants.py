"""CowSwap related addresses and constants."""

#: Assume the same across all chains
COWSWAP_SETTLEMENT = "0x9008D19f58AAbD9eD0D60971565AA8510560ab41"

#: CowSwap has API endpoint per chain
COWSWAP_API_ENDPOINTS = {
    1: "https://api.cow.fi/mainnet",
    0: "https://api.cow.fi/sepolia",
    100: "https://api.cow.fi/xdai",
    42161: "https://api.cow.fi/arbitrum_one",
    8453: "https://api.cow.fi/base",
    137: "https://api.cow.fi/polygon",
    43114: "https://api.cow.fi/avalanche",
}


def get_cowswap_api(chain_id: int) -> str:
    """Get CowSwap API endpoint for given chain ID.

    :param chain_id:
        Chain ID to get the endpoint for

    :return:
        CowSwap API endpoint URL

    :raise KeyError:
        If chain is not supported by CowSwap
    """
    return COWSWAP_API_ENDPOINTS[chain_id]

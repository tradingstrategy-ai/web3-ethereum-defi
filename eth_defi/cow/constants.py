"""CowSwap related addresses and constants."""

#: Assume the same across all chains
COWSWAP_SETTLEMENT = "0x9008D19f58AAbD9eD0D60971565AA8510560ab41"

#: Where do we need to approve tokens to trade via CowSwap
COWSWAP_VAULT_RELAYER = "0xC92E8bdf79f0507f65a392b0ab4667716BFE0110"

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

#: CowSwap explorer URLs per chain id
CHAIN_TO_EXPLORER = {
    1: "https://explorer.cow.fi/ethereum",
    42161: "https://explorer.cow.fi/arb1",
    8453: "https://explorer.cow.fi/base",
    100: "https://explorer.cow.fi/gc",
}

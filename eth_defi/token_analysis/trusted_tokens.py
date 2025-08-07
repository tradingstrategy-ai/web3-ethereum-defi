"""List of alwayas trusted tokens."""

#: Manually whitelist some custodian tokens
#:
#: See :py:func:`is_tradeable_token`.
#:
KNOWN_GOOD_TOKENS = {
    "USDC",
    "USDT",
    "USDS",  # Dai rebranded
    "MKR",
    "DAI",
    "WBTC",
    "NEXO",
    "PEPE",
    "NEXO",
    "AAVE",
    "SYN",
    "SNX",
    "FLOKI",
    "WETH",
    "cbBTC",
    "ETH",
    "WBNB",
}

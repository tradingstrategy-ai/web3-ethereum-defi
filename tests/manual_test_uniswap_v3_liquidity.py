from eth_defi.uniswap_v3.liquidity import estimate_liquidity_depth

for pool_address in [
    # USDC / ETH
    # "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
    # MKR / ETH
    "0xe8c6c9227491c0a8156a0106a0204d881bb7e531",
]:
    estimate_liquidity_depth(pool_address, 14722452, verbose=True)

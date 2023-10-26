# Get the current BNB/BUSD price on PancakeSwap
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware
from eth_defi.uniswap_v2.pair import fetch_pair_details

web3 = Web3(HTTPProvider("https://bsc-dataseed.bnbchain.org"))

print(f"Connected to chain {web3.eth.chain_id}")

# BNB Chain does its own things
install_chain_middleware(web3)

# Find pair addresses on TradingStrategy.ai
# https://tradingstrategy.ai/trading-view/binance/pancakeswap-v2/bnb-busd
pair_address = "0x58f876857a02d6762e0101bb5c46a8c1ed44dc16"
wbnb = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
busd = "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"

# PancakeSwap has this low level encoded token0/token1 as BNB/BUSD
# in human-readable token order
# and we do not need to swap around
reverse_token_order = False

pair = fetch_pair_details(
    web3,
    pair_address,
    reverse_token_order,
)

assert pair.token0.address == wbnb
assert pair.token1.address == busd

price = pair.get_current_mid_price()

# Assume 1 BUSD = 1 USD
print(f"The current price of PancakeSwap BNB/BUSD is {price:.4f} USD")

"""An example to read WETH-USDC price on Uniswap v2 pool on Ethereum mainnet.

- This is a legacy example

- You should really use Uniswap v3 and v4

- You should really use alternative blockchain with less gas fees for trading

"""
import os

from web3 import Web3

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.uniswap_v2.deployment import fetch_deployment
from eth_defi.uniswap_v2.fees import estimate_buy_price
from eth_defi.uniswap_v2.pair import fetch_pair_details

# Default to Ankr free JSON-RPC endpoint if one not given
# https://eth.public-rpc.com/
web3 = create_multi_provider_web3(os.environ.get("JSON_RPC_ETHEREUM", "https://eth.public-rpc.com"))

assert web3.eth.chain_id == 1, f"We are not on Ethereum mainnet, got {web3.eth.chain_id}"

uniswap_v2 = fetch_deployment(
    web3=web3,
    factory_address="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    router_address="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    init_code_hash="0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f",
)

# Uniswap v2's USDC-WETH needs to be reserved to WETH-USDC in human logic
# https://tradingstrategy.ai/trading-view/ethereum/uniswap-v2/eth-usdc
pair = fetch_pair_details(web3, "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc", reverse_token_order=True)
print(f"Uniswap v2 pool is {pair.contract.address}, https://tradingstrategy.ai/trading-view/ethereum/uniswap-v2/eth-usdc")
print("Base token is", pair.get_base_token())
print("Quote token is", pair.get_quote_token())

raw_price = estimate_buy_price(
    uniswap_v2,
    base_token=pair.get_base_token().contract,
    quote_token=pair.get_quote_token().contract,
    quantity=1 * 10**18,  # 1 WETH = 1000000000000000000 wei
)

# Convert raw USDC Solidity amount -> human USDC
human_price = pair.get_quote_token().convert_to_decimals(raw_price)

print(f"Price is {human_price} ETH/USD")

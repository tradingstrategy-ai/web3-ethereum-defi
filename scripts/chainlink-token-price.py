"""Get the latest price of a Chainlink price feed.

"""

import os

from eth_defi.chainlink.token_price import get_token_price_with_chainlink
from eth_defi.provider.multi_provider import create_multi_provider_web3

json_rpc_url = os.environ["JSON_RPC_URL"]

web3 = create_multi_provider_web3(json_rpc_url)

# BNB on Ethereum
base_token_symbol, quote_token_symbol, round_data = get_token_price_with_chainlink(web3, "0x14e613AC84a31f709eadbdF89C6CC390fDc9540A")

price = round_data.price
print(f"The token price of is {price} {base_token_symbol} / {quote_token_symbol}")

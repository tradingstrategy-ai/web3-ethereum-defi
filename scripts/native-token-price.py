"""Get the latest price of a chain native asset.

- Using ChainLink
"""

import os

from eth_defi.chainlink.token_price import get_native_token_price_with_chainlink
from eth_defi.provider.multi_provider import create_multi_provider_web3

json_rpc_url = os.environ["JSON_RPC_URL"]

web3 = create_multi_provider_web3(json_rpc_url)

token_name, last_round = get_native_token_price_with_chainlink(web3)

price = last_round.price
print(f"The chain native token price of is {price} {token_name} / USD")

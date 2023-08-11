"""Check the Uniswap v3 price estimation at a historical block.

- Understand why slippage was what it was

- Check what was the estimated and executed sell WMATIC->USDC on Uniswap v3

- See the TX https://polygonscan.com/tx/0x5b76bf15bce4de5f5d6db8d929f13e28b11816f282ecd1522e4ec6eca3a1655e

"""
import os
from decimal import Decimal

from web3 import Web3, HTTPProvider

from eth_defi.token import fetch_erc20_details
from eth_defi.uniswap_v3.deployment import fetch_deployment
from eth_defi.uniswap_v3.pool import fetch_pool_details
from eth_defi.uniswap_v3.price import get_onchain_price, estimate_sell_received_amount
from eth_defi.uniswap_v3.tvl import fetch_uniswap_v3_pool_tvl

# The test amount of WMATIC for which selling
# we calculate price impact and slippage numbers
wmatic_amount = Decimal("14.975601230579683413")

# WMATIC-USDC 5 BPS pool address
# https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/matic-usdc-fee-5
pool_address = "0xa374094527e1673a86de625aa59517c5de346d32"
block_estimated = 45_583_631  # Assume this was when the trade was deciced
block_executed = 45_583_635  # Assume this was then the trade was executed
wmatic_address = "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"
usdc_address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
fee_tier = 0.0005  # BPS

# Give Polygon archive node JSON-RPC access
json_rpc_url = os.environ["JSON_RPC_POLYGON"]
web3 = Web3(HTTPProvider(json_rpc_url))

wmatic = fetch_erc20_details(web3, wmatic_address)
usdc = fetch_erc20_details(web3, usdc_address)

pool = fetch_pool_details(web3, pool_address)

wmatic_amount_raw = wmatic.convert_to_raw(wmatic_amount)

mid_price_estimated = get_onchain_price(web3, pool_address, block_identifier=block_estimated)
mid_price_executed = get_onchain_price(web3, pool_address, block_identifier=block_executed)

tvl_estimated = fetch_uniswap_v3_pool_tvl(
    pool,
    quote_token=usdc,
    block_identifier=block_estimated,
)

tvl_executed = fetch_uniswap_v3_pool_tvl(
    pool,
    quote_token=usdc,
    block_identifier=block_executed,
)

print(f"WMATIC sold {wmatic_amount}")
print(f"TVL during estimation: {tvl_estimated:,} USDC at block {block_estimated:,}")
print(f"TVL during execution: {tvl_executed:,} USDC")
print(f"Mid price when estimate at block {block_estimated:,} USDC/MATIC:", mid_price_estimated)
print(f"Mid price at the time of execution at block {block_executed:,} USDC/MATIC:", mid_price_executed)
print(f"Mid price movement {(mid_price_executed - mid_price_estimated) / mid_price_estimated * 100:.2f}%")

# Uniswap v3 deployment addresses are the same across the chains
# https://docs.uniswap.org/contracts/v3/reference/deployments
uniswap = fetch_deployment(
    web3,
    "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
    "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
)

# Get the amount of price impact
estimated_sell_raw = estimate_sell_received_amount(
    uniswap,
    base_token_address=wmatic_address,
    quote_token_address=usdc_address,
    quantity=wmatic_amount_raw,
    target_pair_fee=int(fee_tier * 1_000_000),
    block_identifier=block_estimated,
    slippage=0,
)
estimated_sell = usdc.convert_to_decimals(estimated_sell_raw)

print(f"Estimated received quantity: {estimated_sell} USDC")

executed_sell_raw = estimate_sell_received_amount(
    uniswap,
    base_token_address=wmatic_address,
    quote_token_address=usdc_address,
    quantity=wmatic_amount_raw,
    target_pair_fee=int(fee_tier * 1_000_000),
    block_identifier=block_executed,
    slippage=0,
)
executed_sell = usdc.convert_to_decimals(executed_sell_raw)

executed_sell_price = executed_sell / wmatic_amount

print(f"Executed received quantity: {executed_sell} USDC")
print(f"Executed sell price: {executed_sell_price} USDC/MATIC")
print(f"Executed price impact (includes fees) {(executed_sell_price - mid_price_executed) / mid_price_executed * 100:.2f}%")
print(f"Slippage {(executed_sell - estimated_sell) / estimated_sell * 100:.2f}%")

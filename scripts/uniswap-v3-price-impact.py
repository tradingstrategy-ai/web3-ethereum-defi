"""Uniswap v3 price impact example.

- Price impact is the difference between mid price and quoted/filled price

To run:

.. code-block:: shell

    python scripts/uniswap-v3-price-impact.py

"""


import os
from decimal import Decimal

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import fetch_deployment
from eth_defi.uniswap_v3.pool import fetch_pool_details
from eth_defi.uniswap_v3.price import get_onchain_price, estimate_buy_received_amount
from eth_defi.uniswap_v3.tvl import fetch_uniswap_v3_pool_tvl


def main():
    # You can pass your own endpoint in an environment variable
    json_rpc_url = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")

    # Search pair contract addresses using Trading Strategy search: https://tradingstrategy.ai/search
    # This one is:
    # https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/eth-usdc-fee-5
    pool_address = os.environ.get("PAIR_ADDRESS", "0x45dda9cb7c25131df268515131f647d726f50608")

    # Create web3 connection instance
    web3 = create_multi_provider_web3(json_rpc_url)

    contract_details = UNISWAP_V3_DEPLOYMENTS["polygon"]
    uniswap = fetch_deployment(
        web3,
        factory_address=contract_details["factory"],
        router_address=contract_details["router"],
        position_manager_address=contract_details["position_manager"],
        quoter_address=contract_details["quoter"],
    )

    # Get Pool contract ABI file, prepackaged in eth_defi Python package
    # and convert it to a wrapped Python object
    pool = fetch_pool_details(web3, pool_address)

    inverse = True

    # Manually resolve token order from random Uniswap v3 order
    if inverse:
        base_token = pool.token1
        quote_token = pool.token0
    else:
        base_token = pool.token0
        quote_token = pool.token1

    # Print out pool details
    # token0 and token1 will be always in a random order
    # and may inverse the price
    print("-" * 80)
    print("Uniswap pool details")
    print("Chain", web3.eth.chain_id)
    print("Pool", pool_address)
    print("Token0", pool.token0.symbol)
    print("Token1", pool.token1.symbol)
    print("Base token", base_token.symbol)
    print("Quote token", quote_token.symbol)
    print("Fee (BPS)", pool.get_fee_bps())
    print("-" * 80)

    inverse = True  # Is price inverted for output

    # Record the block number close to our timestamp
    block_num = web3.eth.get_block_number()

    # Use get_onchain_price() to get a human readable price
    # in Python Decimal
    mid_price = get_onchain_price(
        web3,
        pool.address,
    )

    if inverse:
        mid_price = 1 / mid_price

    target_pair_fee_bps = 5

    # Attempt to buy ETH wit $1,000,000.50
    swap_amount = Decimal("1_000_000.50")
    swap_amount_raw = quote_token.convert_to_raw(swap_amount)

    received_amount_raw = estimate_buy_received_amount(
        uniswap=uniswap,
        base_token_address=base_token.address,
        quote_token_address=quote_token.address,
        quantity=swap_amount_raw,
        target_pair_fee=target_pair_fee_bps * 100,  # Uniswap v3 units
        block_identifier=block_num,
    )

    received_amount = base_token.convert_to_decimals(received_amount_raw)

    quoted_price = received_amount / swap_amount

    if inverse:
        quoted_price = 1 / quoted_price

    price_impact = (quoted_price - mid_price) / mid_price

    tvl_quote = fetch_uniswap_v3_pool_tvl(
        pool,
        quote_token,
        block_identifier=block_num,
    )

    tvl_base = fetch_uniswap_v3_pool_tvl(
        pool,
        base_token,
        block_identifier=block_num,
    )

    print(f"Block: {block_num:,}")
    print(f"Swap size: {swap_amount:,.2f} {quote_token.symbol}")
    print(f"Pool base token TVL: {tvl_base:,.2f} {base_token.symbol}")
    print(f"Pool quote token TVL: {tvl_quote:,.2f} {quote_token.symbol}")
    print(f"Mid price {base_token.symbol} / {quote_token.symbol}: {mid_price:,.2f}")
    print(f"Quoted amount to received: {received_amount:,.2f} {base_token.symbol}")
    print(f"Quoted price (no execution slippage): {quoted_price:,.2f} {quote_token.symbol}")
    print(f"Price impact: {price_impact * 100:.2f}%")

if __name__ == "__main__":
    main()

"""Debug GraphQL market loading to see which markets are created."""

import logging

logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")

from eth_defi.gmx.ccxt.exchange import GMX

# Create GMX with GraphQL loading
gmx = GMX(params={})

print("\n" + "=" * 60)
print("Markets with 'ETH' in symbol:")
print("=" * 60)

for symbol, market in gmx.markets.items():
    if 'ETH' in symbol.upper():
        market_addr = market['info']['market_token']
        index_addr = market['info']['index_token']
        print(f"\n{symbol}:")
        print(f"  Market address: {market_addr}")
        print(f"  Index address: {index_addr}")

# Check specific addresses
eth_market_addr = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336".lower()
wsteth_market_addr = "0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5".lower()

print("\n" + "=" * 60)
print("Checking specific market addresses:")
print("=" * 60)

for symbol, market in gmx.markets.items():
    market_addr = market['info']['market_token'].lower()
    if market_addr == eth_market_addr:
        print(f"\nETH market address (0x70d95...) found as: {symbol}")
    if market_addr == wsteth_market_addr:
        print(f"\nwstETH market address (0x0cf1...) found as: {symbol}")

print(f"\n\nTotal markets: {len(gmx.markets)}")

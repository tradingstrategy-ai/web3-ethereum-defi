"""Test that GraphQL market loading correctly resolves ETH and wstETH markets."""

import logging
from eth_defi.gmx.ccxt.exchange import GMX

logging.basicConfig(level=logging.DEBUG)


def test_graphql_market_resolution():
    """Test that GraphQL correctly separates ETH and wstETH markets."""
    # Create GMX with GraphQL loading
    gmx = GMX(params={
        'rpcUrl': 'https://arb1.arbitrum.io/rpc',
        'options': {'graphql_only': True}
    })

    # Load markets
    gmx.load_markets()

    # Expected addresses
    ETH_MARKET = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336".lower()
    WSTETH_MARKET = "0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5".lower()
    WSTETH_TOKEN = "0x5979D7b546E38E414F7E9822514be443A4800529".lower()

    # Check ETH market
    eth_market = gmx.markets.get("ETH/USDC:USDC")
    assert eth_market is not None, "ETH market should exist"
    assert eth_market['info']['market_token'].lower() == ETH_MARKET, \
        f"ETH market should be {ETH_MARKET}, got {eth_market['info']['market_token']}"

    # Check wstETH market
    wsteth_market = gmx.markets.get("wstETH/USDC:USDC")
    assert wsteth_market is not None, "wstETH market should exist"
    assert wsteth_market['info']['market_token'].lower() == WSTETH_MARKET, \
        f"wstETH market should be {WSTETH_MARKET}, got {wsteth_market['info']['market_token']}"
    assert wsteth_market['info']['index_token'].lower() == WSTETH_TOKEN, \
        f"wstETH index token should be {WSTETH_TOKEN}, got {wsteth_market['info']['index_token']}"

    print("✓ All assertions passed!")
    print(f"  ETH market: {eth_market['info']['market_token']}")
    print(f"  wstETH market: {wsteth_market['info']['market_token']}")
    print(f"  wstETH index: {wsteth_market['info']['index_token']}")

    return True


if __name__ == "__main__":
    test_graphql_market_resolution()

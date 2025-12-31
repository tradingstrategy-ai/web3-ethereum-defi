"""Benchmark GMX market loading: GraphQL vs RPC.

Compares:
- Loading speed
- Data completeness
- Market resolution correctness (ETH vs wstETH)
"""

import logging
import time
from decimal import Decimal

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.provider.multi_provider import create_multi_provider_web3
from tests.gmx.fork_helpers import setup_mock_oracle

logger = logging.getLogger(__name__)


def benchmark_graphql_loading(web3, test_wallet):
    """Benchmark GraphQL market loading."""
    print("\n" + "=" * 60)
    print("BENCHMARKING GRAPHQL LOADING")
    print("=" * 60)

    # Enable debug logging temporarily
    import logging
    logging.getLogger("eth_defi.gmx.ccxt.exchange").setLevel(logging.DEBUG)

    gmx = GMX(
        params={
            "rpcUrl": web3.provider.endpoint_uri if hasattr(web3.provider, "endpoint_uri") else None,
            "wallet": test_wallet,
            # GraphQL is default, no options needed
        }
    )

    start_time = time.time()
    gmx.load_markets()
    load_time = time.time() - start_time

    # Restore logging
    logging.getLogger("eth_defi.gmx.ccxt.exchange").setLevel(logging.INFO)

    print(f"\n⏱️  Loading time: {load_time:.2f}s")
    print(f"📊 Markets loaded: {len(gmx.markets)}")
    print(f"✓  Markets loaded: {gmx.markets_loaded}")

    # Check specific markets
    eth_market = gmx.markets.get("ETH/USDC:USDC")
    wsteth_market = gmx.markets.get("wstETH/USDC:USDC")
    btc_market = gmx.markets.get("BTC/USDC:USDC")

    print("\nMarket Resolution:")
    if eth_market:
        print(f"  ETH market: {eth_market['info']['market_token']}")
        print(f"    Index token: {eth_market['info']['index_token']}")
        print(f"    Max leverage: {eth_market['limits']['leverage']['max']}")
    else:
        print("  ETH market: NOT FOUND")

    if wsteth_market:
        print(f"  wstETH market: {wsteth_market['info']['market_token']}")
        print(f"    Index token: {wsteth_market['info']['index_token']}")
        print(f"    Max leverage: {wsteth_market['limits']['leverage']['max']}")
    else:
        print("  wstETH market: NOT FOUND")

    if btc_market:
        print(f"  BTC market: {btc_market['info']['market_token']}")
        print(f"    Max leverage: {btc_market['limits']['leverage']['max']}")
    else:
        print("  BTC market: NOT FOUND")

    # Check data fields
    print("\nData Fields Available:")
    if eth_market:
        info_keys = list(eth_market['info'].keys())
        print(f"  Info fields ({len(info_keys)}): {', '.join(info_keys)}")

    return {
        "time": load_time,
        "market_count": len(gmx.markets),
        "eth_market": eth_market,
        "wsteth_market": wsteth_market,
        "btc_market": btc_market,
    }


def benchmark_rpc_loading(web3, test_wallet):
    """Benchmark RPC (Core Markets) loading."""
    print("\n" + "=" * 60)
    print("BENCHMARKING RPC LOADING")
    print("=" * 60)

    gmx = GMX(
        params={
            "rpcUrl": web3.provider.endpoint_uri if hasattr(web3.provider, "endpoint_uri") else None,
            "wallet": test_wallet,
            "options": {
                "graphql_only": False,  # Force RPC loading
            }
        }
    )

    start_time = time.time()
    gmx.load_markets()
    load_time = time.time() - start_time

    print(f"\n⏱️  Loading time: {load_time:.2f}s")
    print(f"📊 Markets loaded: {len(gmx.markets)}")
    print(f"✓  Markets loaded: {gmx.markets_loaded}")

    # Check specific markets
    eth_market = gmx.markets.get("ETH/USDC:USDC")
    wsteth_market = gmx.markets.get("wstETH/USDC:USDC")
    btc_market = gmx.markets.get("BTC/USDC:USDC")

    print("\nMarket Resolution:")
    if eth_market:
        print(f"  ETH market: {eth_market['info']['market_token']}")
        print(f"    Index token: {eth_market['info']['index_token']}")
        print(f"    Max leverage: {eth_market['limits']['leverage']['max']}")
    else:
        print("  ETH market: NOT FOUND")

    if wsteth_market:
        print(f"  wstETH market: {wsteth_market['info']['market_token']}")
        print(f"    Index token: {wsteth_market['info']['index_token']}")
        print(f"    Max leverage: {wsteth_market['limits']['leverage']['max']}")
    else:
        print("  wstETH market: NOT FOUND")

    if btc_market:
        print(f"  BTC market: {btc_market['info']['market_token']}")
        print(f"    Max leverage: {btc_market['limits']['leverage']['max']}")
    else:
        print("  BTC market: NOT FOUND")

    # Check data fields
    print("\nData Fields Available:")
    if eth_market:
        info_keys = list(eth_market['info'].keys())
        print(f"  Info fields ({len(info_keys)}): {', '.join(info_keys)}")

    return {
        "time": load_time,
        "market_count": len(gmx.markets),
        "eth_market": eth_market,
        "wsteth_market": wsteth_market,
        "btc_market": btc_market,
    }


def compare_results(graphql_results, rpc_results):
    """Compare GraphQL vs RPC results."""
    print("\n" + "=" * 60)
    print("COMPARISON")
    print("=" * 60)

    print(f"\n⏱️  Speed:")
    print(f"  GraphQL: {graphql_results['time']:.2f}s")
    print(f"  RPC: {rpc_results['time']:.2f}s")
    speedup = rpc_results['time'] / graphql_results['time'] if graphql_results['time'] > 0 else 0
    print(f"  GraphQL is {speedup:.1f}x faster" if speedup > 1 else f"  RPC is {1/speedup:.1f}x faster")

    print(f"\n📊 Market Count:")
    print(f"  GraphQL: {graphql_results['market_count']}")
    print(f"  RPC: {rpc_results['market_count']}")

    print(f"\n🔍 Market Resolution Correctness:")

    # ETH market
    gql_eth = graphql_results['eth_market']
    rpc_eth = rpc_results['eth_market']
    if gql_eth and rpc_eth:
        gql_addr = gql_eth['info']['market_token'].lower()
        rpc_addr = rpc_eth['info']['market_token'].lower()
        eth_match = gql_addr == rpc_addr
        print(f"  ETH market: {'✓ MATCH' if eth_match else '✗ MISMATCH'}")
        if not eth_match:
            print(f"    GraphQL: {gql_addr}")
            print(f"    RPC: {rpc_addr}")
    else:
        print(f"  ETH market: Missing in {'GraphQL' if not gql_eth else 'RPC'}")

    # wstETH market
    gql_wsteth = graphql_results['wsteth_market']
    rpc_wsteth = rpc_results['wsteth_market']
    if gql_wsteth and rpc_wsteth:
        gql_addr = gql_wsteth['info']['market_token'].lower()
        rpc_addr = rpc_wsteth['info']['market_token'].lower()
        wsteth_match = gql_addr == rpc_addr
        print(f"  wstETH market: {'✓ MATCH' if wsteth_match else '✗ MISMATCH'}")
        if not wsteth_match:
            print(f"    GraphQL: {gql_addr}")
            print(f"    RPC: {rpc_addr}")
    else:
        print(f"  wstETH market: Missing in {'GraphQL' if not gql_wsteth else 'RPC'}")

    print(f"\n📋 Data Completeness:")
    if gql_eth and rpc_eth:
        gql_fields = set(gql_eth['info'].keys())
        rpc_fields = set(rpc_eth['info'].keys())

        print(f"  GraphQL fields: {len(gql_fields)}")
        print(f"  RPC fields: {len(rpc_fields)}")

        only_graphql = gql_fields - rpc_fields
        only_rpc = rpc_fields - gql_fields

        if only_graphql:
            print(f"  Only in GraphQL: {', '.join(only_graphql)}")
        if only_rpc:
            print(f"  Only in RPC: {', '.join(only_rpc)}")
        if not only_graphql and not only_rpc:
            print(f"  ✓ Same fields in both")


def main():
    """Run benchmarks."""
    import os
    from eth_defi.chain import install_chain_middleware
    from eth_defi.gas import node_default_gas_price_strategy
    from eth_defi.hotwallet import HotWallet
    from eth_defi.provider.anvil import fork_network_anvil

    # Use environment variable or default
    rpc_url = os.environ.get("ARBITRUM_CHAIN_JSON_RPC") or os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        print("❌ ARBITRUM_CHAIN_JSON_RPC or JSON_RPC_ARBITRUM environment variable not set")
        print("   Set it to benchmark against real Arbitrum data")
        print("   Example: export ARBITRUM_CHAIN_JSON_RPC=https://arb1.arbitrum.io/rpc")
        return

    print("=" * 60)
    print("GMX MARKET LOADING BENCHMARK")
    print("=" * 60)
    print(f"\nRPC URL: {rpc_url[:50]}...")

    # Create fork
    print("\nSetting up Anvil fork...")
    unlocked_addresses = [
        "0x6DC51f9C50735658Cc6a003e07B0b92dF9c98473",  # Test wallet
    ]

    launch = fork_network_anvil(
        rpc_url,
        unlocked_addresses=unlocked_addresses,
        test_request_timeout=100,
        launch_wait_seconds=60,
    )

    try:
        # Set up web3
        web3 = create_multi_provider_web3(
            launch.json_rpc_url,
            default_http_timeout=(3.0, 180.0),
        )
        install_chain_middleware(web3)
        web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

        # Set up mock oracle
        setup_mock_oracle(web3)

        # Set up test wallet
        test_wallet = HotWallet.create_for_testing(
            web3,
            test_account_n=9,
        )

        # Set up GMX config
        config = GMXConfig(web3, user_wallet_address=test_wallet)

        print(f"✓ Fork ready on {launch.json_rpc_url}")
        print(f"✓ Wallet: {test_wallet.address}")

        # Run benchmarks
        graphql_results = benchmark_graphql_loading(web3, test_wallet)
        rpc_results = benchmark_rpc_loading(web3, test_wallet)

        # Compare
        compare_results(graphql_results, rpc_results)

        print("\n" + "=" * 60)
        print("BENCHMARK COMPLETE")
        print("=" * 60)

    finally:
        launch.close(log_level=logging.ERROR)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    main()

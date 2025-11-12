#!/usr/bin/env python3
"""
Test Hypersync delay vs actual chain head.

This script compares the block height reported by Hypersync vs the actual
chain head to measure any indexing delay.

Usage:
    python test_hypersync_delay.py
"""
import os
from pathlib import Path
from web3 import Web3
import hypersync
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.timestamp import get_hypersync_block_height
from eth_defi.provider.multi_provider import create_multi_provider_web3
from datetime import datetime

# Load .env file if it exists
env_file = Path(__file__).parent.parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key, value.strip('"'))


def test_chain_delay(chain_name: str, rpc_url: str):
    """Test Hypersync delay for a specific chain."""
    print(f"\n{'='*70}")
    print(f"Testing {chain_name.upper()}")
    print(f"{'='*70}")

    # Get actual chain head via RPC
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    actual_block = web3.eth.block_number
    actual_block_data = web3.eth.get_block(actual_block)
    actual_timestamp = datetime.fromtimestamp(actual_block_data['timestamp'])

    print(f"\n🔗 Actual Chain Head (via RPC):")
    print(f"   Block: {actual_block}")
    print(f"   Time:  {actual_timestamp}")

    # Get Hypersync block height
    web3_multi = create_multi_provider_web3(rpc_url)
    hypersync_server = get_hypersync_server(web3_multi)
    config = hypersync.ClientConfig(url=hypersync_server)
    client = hypersync.HypersyncClient(config)

    hypersync_block = get_hypersync_block_height(client)
    hypersync_block_data = web3.eth.get_block(hypersync_block)
    hypersync_timestamp = datetime.fromtimestamp(hypersync_block_data['timestamp'])

    print(f"\n⚡ Hypersync Head:")
    print(f"   Block: {hypersync_block}")
    print(f"   Time:  {hypersync_timestamp}")

    # Calculate delay
    block_delay = actual_block - hypersync_block
    time_delay = actual_timestamp - hypersync_timestamp
    time_delay_seconds = time_delay.total_seconds()

    print(f"\n📊 Delay:")
    print(f"   Blocks behind: {block_delay}")
    print(f"   Time behind:   {time_delay_seconds:.1f} seconds ({time_delay_seconds/60:.1f} minutes)")

    if time_delay_seconds < 60:
        print(f"   ✅ Delay is acceptable (<1 minute)")
        return "good"
    elif time_delay_seconds < 300:
        print(f"   ⚠️  Delay is moderate (1-5 minutes)")
        return "moderate"
    else:
        print(f"   ❌ Delay is high (>5 minutes)")
        return "high"


def main():
    """Test all configured chains."""
    chains = {
        "ethereum": os.getenv("ETH_RPC_URL"),
        "arbitrum": os.getenv("ARBITRUM_RPC_URL"),
        "optimism": os.getenv("OPTIMISM_RPC_URL"),
        "polygon": os.getenv("POLYGON_RPC_URL"),
        "base": os.getenv("BASE_RPC_URL"),
        "avalanche": os.getenv("AVALANCHE_RPC_URL"),
        "bsc": os.getenv("BSC_RPC_URL"),
    }

    results = {}
    for chain_name, rpc_url in chains.items():
        if not rpc_url:
            print(f"\n⚠️  Skipping {chain_name}: No RPC URL configured")
            continue

        try:
            results[chain_name] = test_chain_delay(chain_name, rpc_url)
        except Exception as e:
            print(f"\n❌ Error testing {chain_name}: {e}")
            results[chain_name] = "error"

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for chain, status in results.items():
        emoji = {"good": "✅", "moderate": "⚠️", "high": "❌", "error": "💥"}[status]
        print(f"  {emoji} {chain.capitalize()}: {status}")

    print(f"\n{'='*70}")
    good_count = sum(1 for s in results.values() if s == "good")
    print(f"✅ {good_count}/{len(results)} chains have <1 minute delay")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()

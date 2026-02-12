#!/usr/bin/env python
"""
List all GMX markets for whitelisting in Lagoon vaults.

This script fetches all available GMX perpetual markets and displays them
in various formats suitable for Guard contract whitelisting.

Usage
-----

Basic usage with environment variables::

    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    python scripts/gmx/list-gmx-markets.py

Output as JSON::

    python scripts/gmx/list-gmx-markets.py --json

Output market addresses only (for scripting)::

    python scripts/gmx/list-gmx-markets.py --addresses

Output Python code for copy-pasting::

    python scripts/gmx/list-gmx-markets.py --python

Environment variables
---------------------

- ``JSON_RPC_ARBITRUM``: Arbitrum mainnet RPC endpoint (required)

See also
--------

- :mod:`eth_defi.gmx.whitelist` - GMX market whitelisting module
- :mod:`eth_defi.gmx.core.markets` - Low-level market data fetching
"""

import json
import logging
import os
import sys

from tabulate import tabulate
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.gmx.whitelist import (
    GMX_ARBITRUM_ADDRESSES,
    GMX_POPULAR_MARKETS,
    fetch_all_gmx_markets,
)

# Set up logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def main():
    """Main execution function for listing GMX markets."""

    # Parse simple command line flags
    output_json = "--json" in sys.argv
    output_addresses = "--addresses" in sys.argv
    output_python = "--python" in sys.argv

    # Get RPC URL
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")

    if not rpc_url:
        print("Error: JSON_RPC_ARBITRUM environment variable not set", file=sys.stderr)
        print("\nUsage:", file=sys.stderr)
        print("  export JSON_RPC_ARBITRUM='https://arb1.arbitrum.io/rpc'", file=sys.stderr)
        print("  python scripts/gmx/list-gmx-markets.py", file=sys.stderr)
        sys.exit(1)

    # Handle space-separated multi-provider format
    if " " in rpc_url:
        rpc_url = rpc_url.split()[0]

    # Connect to blockchain
    web3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
    install_chain_middleware(web3)

    if not web3.is_connected():
        print("Error: Failed to connect to RPC", file=sys.stderr)
        sys.exit(1)

    # Fetch markets
    markets = fetch_all_gmx_markets(web3)

    if output_json:
        # JSON output
        output = {
            "chain_id": web3.eth.chain_id,
            "gmx_addresses": GMX_ARBITRUM_ADDRESSES,
            "markets": {
                addr: {
                    "symbol": info.market_symbol,
                    "index_token": info.index_token_address,
                    "long_token": info.long_token_address,
                    "short_token": info.short_token_address,
                }
                for addr, info in markets.items()
            },
        }
        print(json.dumps(output, indent=2))

    elif output_addresses:
        # Plain addresses output
        for addr in markets.keys():
            print(addr)

    elif output_python:
        # Python code output for copy-pasting
        print("# GMX contract addresses for Arbitrum")
        print('GMX_EXCHANGE_ROUTER = "{}"'.format(GMX_ARBITRUM_ADDRESSES["exchange_router"]))
        print('GMX_SYNTHETICS_ROUTER = "{}"'.format(GMX_ARBITRUM_ADDRESSES["synthetics_router"]))
        print('GMX_ORDER_VAULT = "{}"'.format(GMX_ARBITRUM_ADDRESSES["order_vault"]))
        print()
        print("# GMX market addresses")
        print("GMX_MARKETS = {")
        for addr, info in markets.items():
            print(f'    "{info.market_symbol}": "{addr}",')
        print("}")
        print()
        print("# Usage with GMXDeployment:")
        print("from eth_defi.gmx.whitelist import GMXDeployment")
        print()
        print("gmx_deployment = GMXDeployment(")
        print(f'    exchange_router="{GMX_ARBITRUM_ADDRESSES["exchange_router"]}",')
        print(f'    synthetics_router="{GMX_ARBITRUM_ADDRESSES["synthetics_router"]}",')
        print(f'    order_vault="{GMX_ARBITRUM_ADDRESSES["order_vault"]}",')
        print("    markets=[")
        # Show popular markets as examples
        for name, addr in list(GMX_POPULAR_MARKETS.items())[:3]:
            print(f'        "{addr}",  # {name}')
        print("    ],")
        print(")")

    else:
        # Default table output
        print(f"\nGMX Markets on Arbitrum (Chain ID: {web3.eth.chain_id})")
        print(f"Total markets: {len(markets)}\n")

        # GMX contract addresses
        print("GMX Contract Addresses:")
        print(f"  ExchangeRouter:   {GMX_ARBITRUM_ADDRESSES['exchange_router']}")
        print(f"  SyntheticsRouter: {GMX_ARBITRUM_ADDRESSES['synthetics_router']}")
        print(f"  OrderVault:       {GMX_ARBITRUM_ADDRESSES['order_vault']}")
        print()

        # Market table
        table_data = []
        for addr, info in sorted(markets.items(), key=lambda x: x[1].market_symbol):
            # Mark popular markets
            is_popular = addr in GMX_POPULAR_MARKETS.values()
            symbol = f"{info.market_symbol}" + (" *" if is_popular else "")

            table_data.append(
                [
                    symbol,
                    addr,
                    info.long_token_metadata.get("symbol", "?"),
                    info.short_token_metadata.get("symbol", "?"),
                ]
            )

        headers = ["Symbol", "Market Address", "Long Token", "Short Token"]
        print(tabulate(table_data, headers=headers, tablefmt="simple"))

        print("\n* = Popular market (pre-defined in GMX_POPULAR_MARKETS)")
        print("\nUse --python for copy-pasteable Python code")
        print("Use --json for JSON output")
        print("Use --addresses for addresses only")


if __name__ == "__main__":
    main()

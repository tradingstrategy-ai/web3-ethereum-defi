"""
GMX CCXT Limit Order Script

Creates limit orders on GMX using CCXT interface.

This script demonstrates:
1. Creating limit orders using CCXT wrapper
2. Setting trigger prices for order execution
3. Using CCXT-standard parameter format (triggerPrice)

Limit Order Behaviour
---------------------

**Long Limit Order:**
- Set trigger price BELOW current market price
- Order executes when price drops to trigger (buy the dip)
- Example: Market at $2000, trigger at $1900

**Short Limit Order:**
- Set trigger price ABOVE current market price
- Order executes when price rises to trigger (sell the rally)
- Example: Market at $2000, trigger at $2100

Usage
-----

With environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_ccxt_limit_order.py

Environment Variables
---------------------

- ``PRIVATE_KEY``: Your wallet's private key (required)
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint

Examples
--------

**Long Limit Order (buy when price drops):**

.. code-block:: python

    # Fetch current price
    ticker = gmx.fetch_ticker("ETH/USDC:USDC")
    current_price = ticker["last"]  # e.g., $2000

    # Set trigger 5% below current price
    trigger_price = current_price * 0.95  # $1900

    order = gmx.create_order(
        "ETH/USDC:USDC",
        "limit",
        "buy",
        0,  # Ignored when size_usd provided
        trigger_price,  # $1900
        {
            "size_usd": 100,
            "leverage": 2.0,
            "collateral_symbol": "USDC",
        },
    )

**Short Limit Order (sell when price rises):**

.. code-block:: python

    # Set trigger 5% above current price
    trigger_price = current_price * 1.05  # $2100

    order = gmx.create_order(
        "ETH/USDC:USDC",
        "limit",
        "sell",
        0,  # Ignored when size_usd provided
        trigger_price,  # $2100
        {
            "size_usd": 100,
            "leverage": 2.0,
            "collateral_symbol": "USDC",
        },
    )

**Alternative: Using triggerPrice in params:**

.. code-block:: python

    # Both work the same way
    order = gmx.create_order(
        symbol="ETH/USDC:USDC",
        type="limit",
        side="buy",
        amount=0,
        price=None,  # Not used
        params={
            "size_usd": 100,
            "leverage": 2.0,
            "triggerPrice": 1900.0,  # Alternative to price parameter
        },
    )

See Also
--------

- :mod:`eth_defi.gmx.ccxt` - GMX CCXT wrapper
- :mod:`eth_defi.gmx.order.increase_order` - Core limit order implementation
- :py:mod:`scripts.gmx.gmx_limit_order` - Non-CCXT limit order example
"""

import logging
import os
import sys

from rich.logging import RichHandler

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.gas_monitor import GasMonitorConfig
from eth_defi.gmx.contracts import get_token_address_normalized
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from rich.console import Console

console = Console()

# Configuration
EXECUTION_BUFFER = 4  # Higher buffer for testnet reliability
SIZE_USD = 10  # Position size in USD
LEVERAGE = 2.0  # Leverage multiplier
TRIGGER_OFFSET_PERCENT = 0.02  # 2% below current price for long limit
MARKET_SYMBOL = "ETH/USDC:USDC"
COLLATERAL_SYMBOL = "USDC.SG"  # Use USDC.SG as collateral on Sepolia testnet


def verify_order_created(receipt: dict) -> bytes | None:
    """Extract order key from transaction receipt."""
    ORDER_CREATED_HASH = "a7427759bfd3b941f14e687e129519da3c9b0046c5b9aaa290bb1dede63753b3"

    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue

        topic_hashes = []
        for topic in topics:
            if isinstance(topic, bytes):
                topic_hashes.append(topic.hex())
            elif isinstance(topic, str):
                topic_hex = topic[2:] if topic.startswith("0x") else topic
                topic_hashes.append(topic_hex)
            else:
                topic_hashes.append(topic.hex())

        if len(topic_hashes) >= 2 and topic_hashes[1] == ORDER_CREATED_HASH:
            order_key = bytes.fromhex(topic_hashes[2])
            return order_key

    return None


def main():
    # Configure logging to show gas monitoring and trading logs
    FORMAT = "%(message)s"
    logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

    # Enable logging for eth_defi modules (gas monitoring, trading, etc.)
    logging.getLogger("eth_defi").setLevel(logging.INFO)
    logging.getLogger("eth_defi.gmx.trading").setLevel(logging.INFO)
    logging.getLogger("eth_defi.gmx.gas_monitor").setLevel(logging.INFO)
    logging.getLogger("eth_defi.gmx.ccxt.exchange").setLevel(logging.INFO)

    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    private_key = os.environ.get("PRIVATE_KEY")

    if not rpc_url:
        console.print("[red]Error: ARBITRUM_SEPOLIA_RPC_URL environment variable not set[/red]")
        sys.exit(1)

    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    console.print("\n[bold green]=== GMX CCXT Limit Order - Arbitrum Sepolia ===[/bold green]\n")

    # Create web3 provider
    web3 = create_multi_provider_web3(rpc_url)

    # Verify connection
    try:
        block_number = web3.eth.block_number
        chain_id = web3.eth.chain_id
        chain = get_chain_name(chain_id).lower()
        console.print(f"Connected to network")
        console.print(f"  Block: {block_number}")
        console.print(f"  Chain: {chain} (ID: {chain_id})")
    except Exception as e:
        console.print(f"[red]Failed to connect to RPC: {e}[/red]")
        sys.exit(1)

    # Setup wallet
    console.print("\n[bold]Setting up wallet...[/bold]")
    wallet = HotWallet.from_private_key(private_key)
    wallet_address = wallet.get_main_address()
    wallet.sync_nonce(web3)
    console.print(f"  Wallet: {wallet_address}")

    # Check balances
    eth_balance = web3.eth.get_balance(wallet_address)
    console.print(f"  ETH Balance: {eth_balance / 10**18:.6f} ETH")

    # Check USDC balance
    usdc_address = get_token_address_normalized(chain, "USDC")
    usdc_token = fetch_erc20_details(web3, usdc_address)
    usdc_balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
    console.print(f"  USDC Balance: {usdc_balance / 10**6:.2f} USDC")

    # Initialise GMX CCXT wrapper with gas monitoring
    console.print("\n[bold]Initialising GMX CCXT wrapper...[/bold]")
    gas_config = GasMonitorConfig(enabled=True)
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "privateKey": private_key,
            "gas_monitor_config": gas_config,
        }
    )

    # Load markets
    console.print("  Loading markets...")
    gmx.load_markets()
    console.print(f"  Loaded {len(gmx.markets)} markets")

    # Fetch current market price
    console.print("\n[bold]Fetching current market price...[/bold]")
    ticker = gmx.fetch_ticker(MARKET_SYMBOL)
    current_price = ticker["last"]
    console.print(f"  Current {MARKET_SYMBOL} price: ${current_price:,.2f}")

    # Calculate trigger price (2% below for long limit order)
    trigger_price = current_price * (1 - TRIGGER_OFFSET_PERCENT)
    console.print(f"  Limit trigger price: ${trigger_price:,.2f} ({TRIGGER_OFFSET_PERCENT * 100:.1f}% below)")
    console.print(f"\n[dim]Order will execute when price drops to ${trigger_price:,.2f}[/dim]")

    # Create limit order
    console.print(f"\n[bold cyan]Creating Limit Order:[/bold cyan]")
    console.print(f"  Market: {MARKET_SYMBOL}")
    console.print(f"  Collateral: {COLLATERAL_SYMBOL}")
    console.print(f"  Side: Long")
    console.print(f"  Size: ${SIZE_USD}")
    console.print(f"  Leverage: {LEVERAGE}x")
    console.print(f"  Trigger Price: ${trigger_price:,.2f}")
    console.print(f"  Order Type: LIMIT")

    console.print("\n[dim]Using CCXT interface with GMX extensions[/dim]")
    console.print("[dim]Order will be pending until price reaches trigger[/dim]")

    try:
        # Create limit order using CCXT interface
        # Using size_usd parameter for direct USD sizing (GMX native approach)
        order = gmx.create_order(
            MARKET_SYMBOL,
            "limit",
            "buy",
            0,  # Ignored when size_usd is provided
            trigger_price,  # Trigger price for limit order
            {
                "size_usd": SIZE_USD,  # GMX extension: direct USD amount
                "leverage": LEVERAGE,
                "collateral_symbol": COLLATERAL_SYMBOL,
                "execution_buffer": EXECUTION_BUFFER,
                "slippage_percent": 0.005,  # 0.5% slippage
            },
        )

        console.print(f"\n[green]Limit order created successfully![/green]")
        console.print(f"  Status: {order.get('status', 'unknown')}")
        console.print(f"  TX Hash: {order.get('id', 'N/A')}")

        # Extract info safely
        info = order.get("info", {})
        block_num = info.get("block_number") or info.get("blockNumber")
        if block_num:
            console.print(f"  Block: {block_num}")

        # Extract execution fee details
        execution_fee = info.get("execution_fee", 0) / 10**18 if info.get("execution_fee") else 0

        console.print(f"\n[bold]Execution Fee:[/bold]")
        console.print(f"  {execution_fee:.6f} ETH")

        # Verify transaction was successful
        receipt = info.get("receipt", {})
        if receipt and receipt.get("status") != 1:
            console.print("\n[red]Transaction reverted![/red]")
            if order.get("id"):
                assert_transaction_success_with_explanation(web3, order["id"])
            sys.exit(1)

        # Extract order key
        if receipt:
            order_key = verify_order_created(receipt)
            if order_key:
                console.print(f"\n[bold]Order Key:[/bold]")
                console.print(f"  {order_key.hex()}")

        # Summary
        console.print("\n" + "=" * 70)
        console.print("[bold green]Limit Order Created Successfully![/bold green]")
        console.print("=" * 70)
        console.print(f"\n  Market: {MARKET_SYMBOL}")
        console.print(f"  Size: ${SIZE_USD}")
        console.print(f"  Leverage: {LEVERAGE}x")
        console.print(f"  Current Price: ${current_price:,.2f}")
        console.print(f"  Trigger Price: ${trigger_price:,.2f}")
        console.print(f"  Difference: {TRIGGER_OFFSET_PERCENT * 100:.1f}% below current")

        console.print("\n[bold]Next Steps:[/bold]")
        console.print("  1. Order is now pending on GMX")
        console.print(f"  2. When {MARKET_SYMBOL.split('/')[0]} price drops to ${trigger_price:,.2f}, keepers will execute it")
        console.print("  3. Position will open with your specified size and leverage")
        console.print("\n[dim]Check order status on GMX interface using TX hash[/dim]")

        # Show alternative parameter styles
        console.print("\n[bold cyan]Alternative Parameter Styles:[/bold cyan]")
        console.print("\n[dim]1. Price as positional argument (recommended):[/dim]")
        console.print("[dim]   gmx.create_order([/dim]")
        console.print(f'[dim]       "{MARKET_SYMBOL}",[/dim]')
        console.print('[dim]       "limit",[/dim]')
        console.print('[dim]       "buy",[/dim]')
        console.print("[dim]       0,  # amount (ignored with size_usd)[/dim]")
        console.print(f"[dim]       {trigger_price:.2f},  # trigger price[/dim]")
        console.print("[dim]       {...}  # params[/dim]")
        console.print("[dim]   )[/dim]")

        console.print("\n[dim]2. Using triggerPrice in params:[/dim]")
        console.print("[dim]   gmx.create_order([/dim]")
        console.print(f'[dim]       "{MARKET_SYMBOL}",[/dim]')
        console.print('[dim]       "limit",[/dim]')
        console.print('[dim]       "buy",[/dim]')
        console.print("[dim]       0,  # amount[/dim]")
        console.print("[dim]       None,  # price (not used)[/dim]")
        console.print("[dim]       {[/dim]")
        console.print(f'[dim]           "triggerPrice": {trigger_price:.2f},[/dim]')
        console.print('[dim]           "size_usd": 10,[/dim]')
        console.print("[dim]           ...[/dim]")
        console.print("[dim]       }[/dim]")
        console.print("[dim]   )[/dim]")

    except Exception as e:
        console.print(f"\n[red]Error creating limit order: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

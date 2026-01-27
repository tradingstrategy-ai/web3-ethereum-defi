"""
GMX CCXT Stop-Loss and Take-Profit Order Script

Opens a position with bundled SL/TP orders on GMX using CCXT interface.

This script demonstrates:
1. Opening a leveraged position using CCXT wrapper
2. Adding stop-loss and take-profit in a single atomic transaction
3. Using CCXT-standard parameter format (stopLossPrice, takeProfitPrice)

Usage
-----

With environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_ccxt_sltp_order.py

Environment Variables
---------------------

- ``PRIVATE_KEY``: Your wallet's private key (required)
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint

Examples
--------

**Two approaches for position sizing:**

1. **CCXT Standard:** Pass ``amount`` in base currency (ETH)
2. **GMX Extension:** Pass ``size_usd`` in params for direct USD sizing

Approach 1 - CCXT Standard (amount in ETH)::

    # For $1000 position at $2000/ETH:
    ticker = gmx.fetch_ticker("ETH/USDC:USDC")
    amount_eth = 1000 / ticker["last"]  # Convert USD to ETH

    order = gmx.create_market_buy_order(
        "ETH/USDC:USDC",
        amount_eth,  # In base currency (ETH)
        {
            "leverage": 3.0,
            "stopLossPrice": 1850.0,
            "takeProfitPrice": 2200.0,
        },
    )

Approach 2 - GMX Extension (size_usd in params)::

    # Direct USD sizing - no conversion needed
    order = gmx.create_market_buy_order(
        "ETH/USDC:USDC",
        0,  # Ignored when size_usd is provided
        {
            "size_usd": 1000,  # Direct USD amount
            "leverage": 3.0,
            "stopLossPrice": 1850.0,
            "takeProfitPrice": 2200.0,
        },
    )

GMX Extensions (percentage-based triggers with size_usd)::

    order = gmx.create_market_buy_order(
        "ETH/USDC:USDC",
        0,  # Ignored when size_usd is provided
        {
            "size_usd": 1000,  # GMX extension
            "leverage": 3.0,
            "stopLoss": {
                "triggerPercent": 0.05,  # 5% below entry
            },
            "takeProfit": {
                "triggerPercent": 0.10,  # 10% above entry
            },
        },
    )

See Also
--------

- :mod:`eth_defi.gmx.ccxt` - GMX CCXT wrapper
- :mod:`eth_defi.gmx.order.sltp_order` - Core SL/TP implementation
- :py:mod:`scripts.gmx.gmx_stop_loss_order` - Non-CCXT SL/TP example
"""

import logging
import os
import sys

from rich.logging import RichHandler

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.contracts import get_token_address_normalized
from eth_defi.gmx.gas_monitor import GasMonitorConfig
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from rich.console import Console

console = Console()

# Configuration
EXECUTION_BUFFER = 6  # Higher buffer for testnet reliability
SIZE_USD = 10  # Position size in USD
LEVERAGE = 2.0  # Leverage multiplier
STOP_LOSS_PERCENT = 0.05  # 5% stop loss
TAKE_PROFIT_PERCENT = 0.10  # 10% take profit
MARKET_SYMBOL = "ETH/USDC:USDC"
COLLATERAL_SYMBOL = "ETH"  # Use ETH as collateral (long token)


def verify_orders_created(receipt: dict) -> list[bytes]:
    """Extract order keys from transaction receipt."""
    order_keys = []
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
            order_keys.append(order_key)

    return order_keys


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

    console.print("\n[bold green]=== GMX CCXT SL/TP Order - Arbitrum Sepolia ===[/bold green]\n")

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

    # Check USDC.SG balance (testnet uses USDC.SG instead of USDC)
    try:
        usdc_address = get_token_address_normalized(chain, "USDC.SG")
    except KeyError:
        usdc_address = get_token_address_normalized(chain, "USDC")
    usdc_token = fetch_erc20_details(web3, usdc_address)
    usdc_balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
    console.print(f"  {usdc_token.symbol} Balance: {usdc_balance / 10**usdc_token.decimals:.2f} {usdc_token.symbol}")

    # Initialize GMX CCXT wrapper with gas monitoring
    console.print("\n[bold]Initializing GMX CCXT wrapper...[/bold]")
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

    # Calculate SL/TP prices for display
    # We'll fetch current price to show estimated triggers
    console.print("\n[bold]Fetching current market price...[/bold]")
    ticker = gmx.fetch_ticker(MARKET_SYMBOL)
    current_price = ticker["last"]
    console.print(f"  Current {MARKET_SYMBOL} price: ${current_price:,.2f}")

    # Calculate estimated trigger prices (actual will use entry price)
    estimated_sl_trigger = current_price * (1 - STOP_LOSS_PERCENT)
    estimated_tp_trigger = current_price * (1 + TAKE_PROFIT_PERCENT)

    console.print(f"  Estimated SL trigger: ${estimated_sl_trigger:,.2f} ({STOP_LOSS_PERCENT * 100:.1f}% below)")
    console.print(f"  Estimated TP trigger: ${estimated_tp_trigger:,.2f} ({TAKE_PROFIT_PERCENT * 100:.1f}% above)")

    # Create order with bundled SL/TP
    console.print(f"\n[bold cyan]Opening position with SL/TP...[/bold cyan]")
    console.print(f"  Market: {MARKET_SYMBOL}")
    console.print(f"  Collateral: {COLLATERAL_SYMBOL}")
    console.print(f"  Side: Long")
    console.print(f"  Size: ${SIZE_USD}")
    console.print(f"  Leverage: {LEVERAGE}x")
    console.print(f"  Stop Loss: {STOP_LOSS_PERCENT * 100:.1f}% below entry")
    console.print(f"  Take Profit: {TAKE_PROFIT_PERCENT * 100:.1f}% above entry")

    console.print("\n[bold yellow]Creating Bundled Order (3 orders in 1 transaction):[/bold yellow]")
    console.print("  [cyan]1. Main order:[/cyan] Open position")
    console.print("  [cyan]2. Stop Loss order:[/cyan] Triggered at entry - {:.1f}%".format(STOP_LOSS_PERCENT * 100))
    console.print("  [cyan]3. Take Profit order:[/cyan] Triggered at entry + {:.1f}%".format(TAKE_PROFIT_PERCENT * 100))
    console.print("\n[dim]Using GMX extension: percentage-based triggers[/dim]")
    console.print("[dim]Triggers will be calculated from actual entry price[/dim]")

    try:
        # Create market buy order with bundled SL/TP using GMX extensions
        # Using size_usd parameter for direct USD sizing (GMX native approach)
        order = gmx.create_order(
            MARKET_SYMBOL,
            "market",
            "buy",
            0,  # Ignored when size_usd is provided
            None,  # price (not needed for market orders)
            {
                "size_usd": SIZE_USD,  # GMX extension: direct USD amount
                "leverage": LEVERAGE,
                "collateral_symbol": COLLATERAL_SYMBOL,
                "execution_buffer": EXECUTION_BUFFER,
                "slippage_percent": 0.05,  # 5% slippage for testnet reliability
                # GMX extension: percentage-based triggers
                "stopLoss": {
                    "triggerPercent": STOP_LOSS_PERCENT,
                    "closePercent": 1.0,  # Close 100% on stop loss
                    "autoCancel": False,  # Disable auto-cancel to avoid MaxAutoCancelOrdersExceeded
                },
                "takeProfit": {
                    "triggerPercent": TAKE_PROFIT_PERCENT,
                    "closePercent": 1.0,  # Close 100% on take profit
                    "autoCancel": False,  # Disable auto-cancel to avoid MaxAutoCancelOrdersExceeded
                },
            },
        )

        console.print(f"\n[green]Order created successfully![/green]")
        console.print(f"  Status: {order.get('status', 'unknown')}")
        console.print(f"  TX Hash: {order.get('id', 'N/A')}")

        # Extract info safely
        info = order.get("info", {})
        block_num = info.get("block_number") or info.get("blockNumber")
        if block_num:
            console.print(f"  Block: {block_num}")

        # Extract SL/TP details from order info
        entry_price = info.get("entry_price", 0)
        sl_trigger = info.get("stop_loss_trigger_price")
        tp_trigger = info.get("take_profit_trigger_price")
        total_fee = info.get("total_execution_fee", 0) / 10**18 if info.get("total_execution_fee") else 0
        main_fee = info.get("main_order_fee", 0) / 10**18 if info.get("main_order_fee") else 0
        sl_fee = info.get("stop_loss_fee", 0) / 10**18 if info.get("stop_loss_fee") else 0
        tp_fee = info.get("take_profit_fee", 0) / 10**18 if info.get("take_profit_fee") else 0

        if entry_price or sl_trigger or tp_trigger:
            console.print(f"\n[bold]Position Details:[/bold]")
            if entry_price:
                console.print(f"  Entry Price: ${entry_price:,.2f}")
            if sl_trigger:
                console.print(f"  Stop Loss Trigger: ${sl_trigger:,.2f}")
            if tp_trigger:
                console.print(f"  Take Profit Trigger: ${tp_trigger:,.2f}")

        console.print(f"\n[bold]Execution Fees:[/bold]")
        console.print(f"  Main Order: {main_fee:.6f} ETH")
        console.print(f"  Stop Loss: {sl_fee:.6f} ETH")
        console.print(f"  Take Profit: {tp_fee:.6f} ETH")
        console.print(f"  Total: {total_fee:.6f} ETH")

        # Verify transaction was successful
        receipt = info.get("receipt", {})
        if receipt and receipt.get("status") != 1:
            console.print("\n[red]Transaction reverted![/red]")
            if order.get("id"):
                assert_transaction_success_with_explanation(web3, order["id"])
            sys.exit(1)

        # Extract order keys
        if receipt:
            order_keys = verify_orders_created(receipt)
            if order_keys:
                console.print(f"\n[bold]Orders Created:[/bold]")
                for idx, key in enumerate(order_keys, 1):
                    order_type = ["Main", "Stop Loss", "Take Profit"][idx - 1] if idx <= 3 else f"Order {idx}"
                    console.print(f"  {order_type}: {key.hex()}")

        # Summary
        console.print("\n" + "=" * 60)
        console.print("[bold green]Position with SL/TP Created Successfully![/bold green]")
        console.print("=" * 60)
        console.print(f"\n  Market: {MARKET_SYMBOL}")
        console.print(f"  Size: ${SIZE_USD}")
        console.print(f"  Leverage: {LEVERAGE}x")

        if entry_price:
            console.print(f"  Entry Price: ${entry_price:,.2f}")
        if sl_trigger and entry_price and entry_price > 0:
            console.print(f"  Stop Loss: ${sl_trigger:,.2f} ({((sl_trigger / entry_price - 1) * 100):.2f}%)")
        elif sl_trigger:
            console.print(f"  Stop Loss: ${sl_trigger:,.2f}")
        if tp_trigger and entry_price and entry_price > 0:
            console.print(f"  Take Profit: ${tp_trigger:,.2f} ({((tp_trigger / entry_price - 1) * 100):.2f}%)")
        elif tp_trigger:
            console.print(f"  Take Profit: ${tp_trigger:,.2f}")

        console.print("\n[dim]Note: Orders are pending until keepers execute them.[/dim]")
        console.print("[dim]- Main order will be executed first to open the position[/dim]")
        console.print("[dim]- SL/TP orders will activate when price triggers are met[/dim]")

        # Show alternative parameter styles
        console.print("\n[bold cyan]Alternative Parameter Styles:[/bold cyan]")
        console.print("\n[dim]1. CCXT Unified Style (simple prices):[/dim]")
        console.print("[dim]   params = {[/dim]")
        console.print('[dim]       "stopLossPrice": 1850.0,[/dim]')
        console.print('[dim]       "takeProfitPrice": 2200.0,[/dim]')
        console.print("[dim]   }[/dim]")

        console.print("\n[dim]2. CCXT Object Style (with options):[/dim]")
        console.print("[dim]   params = {[/dim]")
        console.print('[dim]       "stopLoss": {"triggerPrice": 1850.0},[/dim]')
        console.print('[dim]       "takeProfit": {"triggerPrice": 2200.0},[/dim]')
        console.print("[dim]   }[/dim]")

        console.print("\n[dim]3. GMX Extension (percentage-based):[/dim]")
        console.print("[dim]   params = {[/dim]")
        console.print('[dim]       "stopLoss": {"triggerPercent": 0.05},  # 5% below entry[/dim]')
        console.print('[dim]       "takeProfit": {"triggerPercent": 0.10},  # 10% above entry[/dim]')
        console.print("[dim]   }[/dim]")

    except Exception as e:
        console.print(f"\n[red]Error creating order: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

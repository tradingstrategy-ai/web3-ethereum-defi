"""
GMX CCXT Open Position Script

This script opens a GMX position using CCXT-compatible configuration.
It reads configuration from a JSON file passed as a CLI argument.

Usage
-----

    python scripts/gmx/gmx_ccxt_open_position.py /path/to/config.json

Configuration File
------------------

The config file should have this structure::

    {"exchange": {"ccxt_config": {"rpcUrl": "https://arb1.arbitrum.io/rpc", "privateKey": "0x...", "symbol": "HYPE/USDC:USDC", "side": "buy", "positionSize": 10.0, "leverage": 2.5, "collateralSymbol": "HYPE", "slippagePercent": 0.5, "executionBuffer": 2.2}}}

Required fields:
- ``rpcUrl``: RPC endpoint URL
- ``privateKey``: Wallet private key with 0x prefix

Optional fields (can be overridden by environment variables):
- ``symbol``: Trading pair symbol (default: "HYPE/USDC:USDC")
- ``side``: Trade side "buy" or "sell" (default: "buy")
- ``positionSize``: Position size in USD (default: 10.0)
- ``leverage``: Leverage multiplier (default: 2.5)
- ``collateralSymbol``: Collateral token symbol (default: "HYPE")
- ``slippagePercent``: Slippage tolerance as percent (default: 0.5)
- ``executionBuffer``: Execution fee buffer multiplier (default: 2.2)

Example
-------

    # Open a long position using config file
    poetry run python scripts/gmx/gmx_ccxt_open_position.py /path/to/config.json

Environment Variables
---------------------

Environment variables override config file values:

- ``SYMBOL``: Trading pair symbol
- ``SIDE``: Trade side "buy" or "sell"
- ``POSITION_SIZE``: Position size in USD
- ``LEVERAGE``: Leverage multiplier
- ``COLLATERAL_SYMBOL``: Collateral token symbol
- ``SLIPPAGE_PERCENT``: Slippage tolerance as percent
- ``EXECUTION_BUFFER``: Execution fee buffer multiplier
"""

import json
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)
console = Console()


def load_config(config_path: str) -> dict:
    """Load and validate configuration from JSON file.

    :param config_path:
        Path to the configuration JSON file
    :return:
        Dictionary containing exchange configuration
    :raises FileNotFoundError:
        If config file doesn't exist
    :raises ValueError:
        If required keys are missing
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        config = json.load(f)

    # Validate required keys
    if "exchange" not in config:
        raise ValueError("Missing 'exchange' key in config file")
    if "ccxt_config" not in config["exchange"]:
        raise ValueError("Missing 'exchange.ccxt_config' in config file")

    ccxt_config = config["exchange"]["ccxt_config"]
    if "rpcUrl" not in ccxt_config:
        raise ValueError("Missing 'exchange.ccxt_config.rpcUrl' in config file")
    if "privateKey" not in ccxt_config:
        raise ValueError("Missing 'exchange.ccxt_config.privateKey' in config file")

    return config


def main():
    """Main entry point."""
    # Configure logging
    FORMAT = "%(message)s"
    logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

    # Enable logging for eth_defi modules
    logging.getLogger("eth_defi").setLevel(logging.INFO)
    logging.getLogger("eth_defi.gmx.trading").setLevel(logging.INFO)

    # Check CLI arguments
    if len(sys.argv) < 2:
        console.print("[red]Usage: python gmx_ccxt_open_position.py <config_file>[/red]")
        console.print("Example: poetry run python scripts/gmx/gmx_ccxt_open_position.py /path/to/config.json")
        sys.exit(1)

    config_path = sys.argv[1]

    console.print("\n[bold]GMX CCXT Open Position Script[/bold]\n")
    console.print(f"Loading config from: {config_path}")

    # Load config
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        sys.exit(1)

    ccxt_config = config["exchange"]["ccxt_config"]
    rpc_url = ccxt_config["rpcUrl"]
    private_key = ccxt_config["privateKey"]

    # Get trading parameters from config file, with environment variables as fallback
    symbol = os.environ.get("SYMBOL", ccxt_config.get("symbol", "HYPE/USDC:USDC"))
    side = os.environ.get("SIDE", ccxt_config.get("side", "buy"))  # buy = long, sell = short
    position_size = float(os.environ.get("POSITION_SIZE", ccxt_config.get("positionSize", 10.0)))
    leverage = float(os.environ.get("LEVERAGE", ccxt_config.get("leverage", 2.5)))
    collateral_symbol = os.environ.get("COLLATERAL_SYMBOL", ccxt_config.get("collateralSymbol", "HYPE"))
    slippage_percent = float(os.environ.get("SLIPPAGE_PERCENT", ccxt_config.get("slippagePercent", 0.5))) / 100.0
    execution_buffer = float(os.environ.get("EXECUTION_BUFFER", ccxt_config.get("executionBuffer", 2.2)))

    console.print(f"\n[cyan]Trading Parameters:[/cyan]")
    console.print(f"  Symbol: {symbol}")
    console.print(f"  Side: {side.upper()}")
    console.print(f"  Position size: ${position_size:.2f} USD")
    console.print(f"  Leverage: {leverage}x")
    console.print(f"  Collateral: {collateral_symbol}")
    console.print(f"  Slippage: {slippage_percent * 100:.2f}%")
    console.print(f"  Execution buffer: {execution_buffer}x")

    # Connect to blockchain
    console.print("\n[blue]Connecting to blockchain...[/blue]")
    web3 = create_multi_provider_web3(rpc_url)

    try:
        block_number = web3.eth.block_number
        chain_id = web3.eth.chain_id
        chain_name = get_chain_name(chain_id)
        console.print(f"[green]Connected![/green] Chain: {chain_name}, Chain ID: {chain_id}, Block: {block_number}")
    except Exception as e:
        console.print(f"[red]Failed to connect to RPC: {e}[/red]")
        sys.exit(1)

    # Create wallet
    wallet = HotWallet.from_private_key(private_key)
    wallet_address = wallet.get_main_address()
    wallet.sync_nonce(web3)

    console.print(f"Wallet address: {wallet_address}")

    # Check ETH balance for gas
    eth_balance = web3.eth.get_balance(wallet_address)
    eth_balance_float = float(web3.from_wei(eth_balance, "ether"))
    console.print(f"ETH balance: {eth_balance_float:.6f}")

    if eth_balance_float < 0.001:
        console.print("[yellow]Warning: Low ETH balance for gas fees![/yellow]")

    # Initialise GMX with CCXT-style parameters
    console.print("\n[blue]Initialising GMX CCXT adapter...[/blue]")
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "privateKey": private_key,
            "verbose": False,
        }
    )

    console.print("[green]GMX initialised successfully[/green]")

    # Load markets
    console.print("\n[blue]Loading available markets...[/blue]")
    gmx.load_markets()
    console.print(f"[green]Loaded {len(gmx.markets)} markets[/green]")

    # Verify symbol exists
    if symbol not in gmx.markets:
        console.print(f"[red]Error: Symbol '{symbol}' not found in available markets[/red]")
        console.print("\nAvailable markets:")
        for market_symbol in list(gmx.markets.keys())[:10]:
            console.print(f"  - {market_symbol}")
        sys.exit(1)

    # Display market info
    market = gmx.markets[symbol]
    market_table = Table(title=f"Market: {symbol}")
    market_table.add_column("Property", style="cyan")
    market_table.add_column("Value", style="green")

    market_table.add_row("Type", market.get("type", "N/A"))
    market_table.add_row("Base", market.get("base", "N/A"))
    market_table.add_row("Quote", market.get("quote", "N/A"))
    market_table.add_row("Settle", market.get("settle", "N/A"))

    console.print(market_table)

    # Confirm before opening position
    console.print(f"\n[yellow]About to open {side.upper()} position:[/yellow]")
    console.print(f"  Symbol: {symbol}")
    console.print(f"  Size: ${position_size:.2f} USD")
    console.print(f"  Leverage: {leverage}x")
    console.print(f"  Collateral: {collateral_symbol}")
    console.print("\n[yellow]Press Enter to continue or Ctrl+C to abort...[/yellow]")

    try:
        input()
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted by user.[/yellow]")
        sys.exit(0)

    # Create order
    console.print(f"\n[blue]Creating {side.upper()} order...[/blue]")

    try:
        if side.lower() == "buy":
            order = gmx.create_market_buy_order(
                symbol,
                position_size,
                {
                    "leverage": leverage,
                    "collateral_symbol": collateral_symbol,
                    "slippage_percent": slippage_percent,
                    "execution_buffer": execution_buffer,
                },
            )
        elif side.lower() == "sell":
            order = gmx.create_market_sell_order(
                symbol,
                position_size,
                {
                    "leverage": leverage,
                    "collateral_symbol": collateral_symbol,
                    "slippage_percent": slippage_percent,
                    "execution_buffer": execution_buffer,
                },
            )
        else:
            console.print(f"[red]Invalid side: {side}. Must be 'buy' or 'sell'[/red]")
            sys.exit(1)

        console.print(f"[green]Order created successfully![/green]")

        # Display order details
        order_table = Table(title="Order Details")
        order_table.add_column("Property", style="cyan")
        order_table.add_column("Value", style="green")

        order_table.add_row("Order ID", order["id"][:16] + "..." if len(order["id"]) > 16 else order["id"])
        order_table.add_row("Status", order["status"])
        order_table.add_row("Symbol", order["symbol"])
        order_table.add_row("Side", order["side"].upper())
        order_table.add_row("Amount", f"${order['amount']:.2f}")
        order_table.add_row("Fee", f"${order['fee']['cost']:.4f}" if order.get("fee") else "N/A")
        order_table.add_row("TX Hash", order["info"]["tx_hash"])

        console.print(order_table)

        # Verify transaction success
        if order.get("id"):
            console.print("\n[blue]Verifying transaction...[/blue]")
            assert_transaction_success_with_explanation(web3, order["id"])
            console.print("[green]Transaction verified successfully[/green]")

        console.print("\n[yellow]Note: Order created on-chain. In production, a keeper will execute it.[/yellow]")
        console.print("[yellow]The position will be opened once the keeper executes the order.[/yellow]")

        console.print("\n[bold green]Order creation completed successfully![/bold green]")

    except Exception as e:
        console.print(f"[red]Failed to create order: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

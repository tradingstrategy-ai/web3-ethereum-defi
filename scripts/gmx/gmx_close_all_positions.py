"""
GMX Close All Positions Script

This script closes all open GMX positions for a given wallet.
It reads configuration from a JSON secrets file passed as a CLI argument.

Usage
-----

    python scripts/gmx/gmx_close_all_positions.py /path/to/secrets.json [slippage_percent]

The secrets file should have this structure::

    {"exchange": {"ccxt_config": {"rpcUrl": "https://...", "privateKey": "0x..."}}}

Example
-------

    # Default 1% slippage
    poetry run python scripts/gmx/gmx_close_all_positions.py /path/to/secrets.json

    # Custom 3% slippage
    poetry run python scripts/gmx/gmx_close_all_positions.py /path/to/secrets.json 3.0

"""

import json
import logging
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from rich.logging import RichHandler

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.events import extract_order_key_from_receipt
from eth_defi.gmx.gas_monitor import GasMonitorConfig
from eth_defi.gmx.order_tracking import check_order_status, is_order_pending
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.verification import verify_gmx_order_execution
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3

# Configure logging to show gas monitoring and trading logs
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

# Enable logging for eth_defi modules (gas monitoring, trading, etc.)
logging.getLogger("eth_defi").setLevel(logging.INFO)
logging.getLogger("eth_defi.gmx.trading").setLevel(logging.INFO)
logging.getLogger("eth_defi.gmx.gas_monitor").setLevel(logging.INFO)

logger = logging.getLogger(__name__)
console = Console()


def load_secrets(secrets_path: str) -> dict:
    """Load and validate secrets from JSON file.

    :param secrets_path:
        Path to the secrets JSON file
    :return:
        Dictionary containing exchange configuration
    :raises FileNotFoundError:
        If secrets file doesn't exist
    :raises ValueError:
        If required keys are missing
    """
    path = Path(secrets_path)
    if not path.exists():
        raise FileNotFoundError(f"Secrets file not found: {secrets_path}")

    with open(path) as f:
        secrets = json.load(f)

    # Validate required keys
    if "exchange" not in secrets:
        raise ValueError("Missing 'exchange' key in secrets file")
    if "ccxt_config" not in secrets["exchange"]:
        raise ValueError("Missing 'exchange.ccxt_config' in secrets file")

    ccxt_config = secrets["exchange"]["ccxt_config"]
    if "rpcUrl" not in ccxt_config:
        raise ValueError("Missing 'exchange.ccxt_config.rpcUrl' in secrets file")
    if "privateKey" not in ccxt_config:
        raise ValueError("Missing 'exchange.ccxt_config.privateKey' in secrets file")

    return secrets


def display_positions(positions: dict) -> None:
    """Display positions in a formatted table.

    :param positions:
        Dictionary of position key -> position data
    """
    if not positions:
        console.print("[yellow]No open positions found.[/yellow]")
        return

    table = Table(title="Open Positions")
    table.add_column("Position Key", style="cyan")
    table.add_column("Market", style="magenta")
    table.add_column("Collateral", style="green")
    table.add_column("Type", style="yellow")
    table.add_column("Size (USD)", style="blue")
    table.add_column("Leverage", style="red")
    table.add_column("Entry Price", style="white")
    table.add_column("Mark Price", style="white")
    table.add_column("P&L %", style="white")

    for position_key, position_data in positions.items():
        pnl_color = "green" if position_data["percent_profit"] > 0 else "red"
        table.add_row(
            position_key,
            position_data["market_symbol"],
            position_data["collateral_token"],
            "LONG" if position_data["is_long"] else "SHORT",
            f"${position_data['position_size']:.2f}",
            f"{position_data['leverage']:.2f}x",
            f"${position_data['entry_price']:.4f}",
            f"${position_data['mark_price']:.4f}",
            f"[{pnl_color}]{position_data['percent_profit']:.2f}%[/{pnl_color}]",
        )

    console.print(table)


def close_position(
    trading_client: GMXTrading,
    wallet: HotWallet,
    web3,
    chain: str,
    position_key: str,
    position_data: dict,
    slippage_percent: float = 0.01,
    max_wait_seconds: int = 120,
) -> bool:
    """Close a single position.

    :param trading_client:
        GMXTrading instance
    :param wallet:
        HotWallet for signing transactions
    :param web3:
        Web3 instance
    :param chain:
        Chain name (e.g., "arbitrum", "avalanche")
    :param position_key:
        Position key (e.g., "ETH_long")
    :param position_data:
        Position data dictionary
    :param slippage_percent:
        Slippage tolerance as decimal (0.01 = 1%)
    :param max_wait_seconds:
        Maximum time to wait for keeper execution
    :return:
        True if successfully closed, False otherwise
    """
    console.print(f"\n[blue]Closing position: {position_key}[/blue]")
    console.print(f"  Market: {position_data['market_symbol']}")
    console.print(f"  Type: {'LONG' if position_data['is_long'] else 'SHORT'}")
    console.print(f"  Size: ${position_data['position_size']:.2f}")

    try:
        # Extract position details
        market_symbol = position_data["market_symbol"]
        collateral_symbol = position_data["collateral_token"]
        is_long = position_data["is_long"]
        size_usd = position_data["position_size"]

        # Define reverse mapping: positions store tokens with their actual contract names
        # but the trading API expects these symbols
        reverse_symbol_mapping = {
            "WETH": "ETH",
            "BTC": "WBTC",
        }

        # Apply reverse mapping to market symbol if needed
        if market_symbol in reverse_symbol_mapping:
            console.print(f"  Mapping market symbol '{market_symbol}' -> '{reverse_symbol_mapping[market_symbol]}'")
            market_symbol = reverse_symbol_mapping[market_symbol]

        # Calculate collateral delta from position size and leverage
        leverage = position_data.get("leverage", 1.0)
        if leverage > 100 or leverage < 0.1:
            console.print(f"  [yellow]Warning: Abnormal leverage {leverage:.2f}x, using size/10[/yellow]")
            initial_collateral_delta = size_usd / 10
        else:
            initial_collateral_delta = size_usd / leverage

        if initial_collateral_delta < 0.1:
            initial_collateral_delta = size_usd

        # Use collateral token as start token for simplicity
        start_token_symbol = collateral_symbol

        console.print(f"  Slippage: {slippage_percent * 100:.1f}%")

        # Create close order
        order_result = trading_client.close_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=start_token_symbol,
            is_long=is_long,
            size_delta_usd=size_usd,
            initial_collateral_delta=initial_collateral_delta,
            slippage_percent=slippage_percent,
            execution_buffer=2.2,
        )

        # Sign and send transaction
        transaction = order_result.transaction.copy()
        if "nonce" in transaction:
            del transaction["nonce"]

        signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

        console.print(f"  Order creation tx: [yellow]{tx_hash.hex()}[/yellow]")

        # Wait for order creation confirmation
        console.print("  Waiting for order creation...")
        creation_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        if creation_receipt["status"] != 1:
            console.print("  [red]Order creation transaction reverted![/red]")
            return False

        console.print(f"  Order created in block {creation_receipt['blockNumber']}")

        # Extract order key from OrderCreated event
        try:
            order_key = extract_order_key_from_receipt(web3, creation_receipt)
            console.print(f"  Order key: [cyan]{order_key.hex()[:16]}...[/cyan]")
        except ValueError as e:
            console.print(f"  [red]Failed to extract order key: {e}[/red]")
            return False

        # Wait for keeper execution (GMX orders are executed in a separate tx)
        console.print("  Waiting for keeper execution...")
        start_time = time.time()
        poll_interval = 2  # seconds

        while time.time() - start_time < max_wait_seconds:
            if not is_order_pending(web3, order_key, chain):
                break
            elapsed = int(time.time() - start_time)
            console.print(f"  Still pending... ({elapsed}s)")
            time.sleep(poll_interval)
        else:
            console.print(f"  [yellow]Timed out waiting for keeper ({max_wait_seconds}s)[/yellow]")
            console.print("  Order may still be executed later")
            return False

        # Get the execution receipt
        status_result = check_order_status(web3, order_key, chain)

        if not status_result.execution_receipt:
            console.print("  [yellow]Order executed but receipt not found[/yellow]")
            return False

        console.print(f"  Keeper tx: [yellow]{status_result.execution_tx_hash[:16]}...[/yellow]")

        # Verify GMX order execution - tx can succeed but order can be cancelled/frozen
        console.print("  Verifying GMX order execution...")
        verification = verify_gmx_order_execution(web3, status_result.execution_receipt, order_key)

        if verification.success:
            console.print(f"  [green]Successfully closed![/green] Block: {status_result.execution_block}")
            if verification.execution_price:
                console.print(f"  Execution price: ${verification.execution_price:.4f}")
            if verification.size_delta_usd:
                console.print(f"  Size closed: ${verification.size_delta_usd:.2f}")
            if verification.pnl_usd is not None:
                pnl_color = "green" if verification.pnl_usd >= 0 else "red"
                console.print(f"  Realised PnL: [{pnl_color}]${verification.pnl_usd:.2f}[/{pnl_color}]")
            return True
        else:
            console.print(f"  [red]Order {verification.status}![/red]")
            if verification.decoded_error:
                console.print(f"  [red]Reason: {verification.decoded_error}[/red]")
            elif verification.reason:
                console.print(f"  [red]Reason: {verification.reason}[/red]")
            return False

    except Exception as e:
        console.print(f"  [red]Error closing position: {e}[/red]")
        logger.exception("Failed to close position %s", position_key)
        return False


def main():
    """Main entry point."""
    # Check CLI arguments
    if len(sys.argv) < 2:
        console.print("[red]Usage: python gmx_close_all_positions.py <secrets_file> [slippage_percent][/red]")
        console.print("Example: poetry run python scripts/gmx/gmx_close_all_positions.py /path/to/secrets.json")
        console.print("Example: poetry run python scripts/gmx/gmx_close_all_positions.py /path/to/secrets.json 3.0")
        sys.exit(1)

    secrets_path = sys.argv[1]

    # Parse slippage (default 1%)
    slippage_percent = 0.01
    if len(sys.argv) >= 3:
        try:
            slippage_percent = float(sys.argv[2]) / 100.0  # Convert from percent to decimal
        except ValueError:
            console.print(f"[red]Invalid slippage value: {sys.argv[2]}. Using default 1%.[/red]")
            slippage_percent = 0.01

    console.print("\n[bold]GMX Close All Positions Script[/bold]\n")
    console.print(f"Loading secrets from: {secrets_path}")
    console.print(f"Slippage tolerance: {slippage_percent * 100:.1f}%")

    # Load secrets
    try:
        secrets = load_secrets(secrets_path)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error loading secrets: {e}[/red]")
        sys.exit(1)

    ccxt_config = secrets["exchange"]["ccxt_config"]
    rpc_url = ccxt_config["rpcUrl"]
    private_key = ccxt_config["privateKey"]

    # Connect to blockchain
    console.print("Connecting to blockchain...")
    web3 = create_multi_provider_web3(rpc_url)

    try:
        block_number = web3.eth.block_number
        chain_id = web3.eth.chain_id
        chain = get_chain_name(chain_id).lower()
        console.print(f"Connected! Chain: {chain}, Chain ID: {chain_id}, Block: {block_number}")
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
        console.print("[red]Warning: Low ETH balance for gas fees![/red]")

    # Create GMX config and trading client with gas monitoring
    config = GMXConfig(web3, user_wallet_address=wallet_address, wallet=wallet)
    gas_config = GasMonitorConfig(enabled=True)
    trading_client = GMXTrading(config, gas_monitor_config=gas_config)

    # Fetch open positions
    console.print("\nFetching open positions...")
    positions_fetcher = GetOpenPositions(config)
    positions = positions_fetcher.get_data(wallet_address)

    display_positions(positions)

    if not positions:
        console.print("\n[green]No positions to close. Done![/green]")
        sys.exit(0)

    # Confirm before closing
    console.print(f"\n[yellow]Found {len(positions)} position(s) to close.[/yellow]")
    console.print("[yellow]Press Enter to continue or Ctrl+C to abort...[/yellow]")

    try:
        input()
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted by user.[/yellow]")
        sys.exit(0)

    # Close each position
    console.print("\n[bold]Closing positions...[/bold]")

    closed = 0
    failed = 0

    for position_key, position_data in positions.items():
        success = close_position(
            trading_client=trading_client,
            wallet=wallet,
            web3=web3,
            chain=chain,
            position_key=position_key,
            position_data=position_data,
            slippage_percent=slippage_percent,
            max_wait_seconds=40,
        )

        if success:
            closed += 1
        else:
            failed += 1

    # Summary
    console.print("\n" + "=" * 50)
    console.print("[bold]Summary[/bold]")
    console.print(f"  Positions closed: [green]{closed}[/green]")
    if failed > 0:
        console.print(f"  Positions failed: [red]{failed}[/red]")
    console.print("=" * 50)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

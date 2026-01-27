"""
GMX Gas Estimation Script

Shows gas monitoring in action and estimates costs for opening/closing positions.
No transactions are sent - this is estimation only.

Usage
-----

    export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
    export PRIVATE_KEY=<your-private-key>
    poetry run python scripts/gmx/gmx_gas_estimation.py

"""

import logging
import os
import sys

from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.gas_monitor import GasMonitorConfig, GMXGasMonitor
from eth_defi.hotwallet import HotWallet

console = Console()


def main():
    # Configure logging
    FORMAT = "%(message)s"
    logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
    logging.getLogger("eth_defi.gmx.gas_monitor").setLevel(logging.INFO)

    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    private_key = os.environ.get("PRIVATE_KEY")

    if not rpc_url:
        console.print("[red]Error: JSON_RPC_ARBITRUM environment variable not set[/red]")
        console.print("Run: export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC")
        sys.exit(1)

    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        console.print("Run: export PRIVATE_KEY=<your-private-key>")
        sys.exit(1)

    console.print("\n[bold cyan]=== GMX Gas Monitoring Demo ===[/bold cyan]\n")

    # Connect to mainnet
    web3 = create_multi_provider_web3(rpc_url)
    chain_id = web3.eth.chain_id
    chain = get_chain_name(chain_id).lower()

    console.print(f"Connected to {chain} (chain ID: {chain_id})")
    console.print(f"Current block: {web3.eth.block_number}")

    # Get current gas price
    gas_price = web3.eth.gas_price
    gas_price_gwei = gas_price / 10**9
    console.print(f"Current gas price: {gas_price_gwei:.4f} gwei\n")

    # Create wallet from private key
    wallet = HotWallet.from_private_key(private_key)
    wallet_address = wallet.address

    console.print(f"[bold]Your wallet:[/bold] {wallet_address}\n")

    # Create GMX config with gas monitoring enabled
    config = GMXConfig(web3, user_wallet_address=wallet_address)
    gas_monitor_config = GasMonitorConfig(
        enabled=True,
        warning_threshold_usd=10.0,
        critical_threshold_usd=2.0,
        gas_estimate_buffer=1.2,
    )
    trading = GMXTrading(config, gas_monitor_config=gas_monitor_config)

    # Create gas monitor
    gas_monitor = GMXGasMonitor(web3, chain, gas_monitor_config)

    # Get ETH price
    eth_price = gas_monitor.get_native_token_price_usd()
    if eth_price:
        console.print(f"ETH price: ${eth_price:,.2f}")
    else:
        console.print("[yellow]Warning: Could not fetch ETH price from oracle[/yellow]")
        eth_price = 3500.0  # Fallback

    # Check gas balance
    console.print("\n[bold yellow]━━━ Gas Balance Check ━━━[/bold yellow]")
    gas_check = gas_monitor.check_gas_balance(wallet_address)
    console.print(f"Balance: {gas_check.native_balance:.6f} ETH", end="")
    if gas_check.balance_usd:
        console.print(f" (~${gas_check.balance_usd:.2f})")
    else:
        console.print()

    if gas_check.status == "critical":
        console.print(f"Status: [bold red]⚠ CRITICAL[/bold red] - Below ${gas_monitor_config.critical_threshold_usd} threshold!")
        console.print("[yellow]You may not have enough ETH for trading operations[/yellow]")
    elif gas_check.status == "warning":
        console.print(f"Status: [bold yellow]⚠ WARNING[/bold yellow] - Below ${gas_monitor_config.warning_threshold_usd} threshold")
    else:
        console.print(f"Status: [bold green]✓ OK[/bold green]")

    # Position parameters
    console.print("\n[bold yellow]━━━ Position Parameters ━━━[/bold yellow]")
    market_symbol = "ETH"
    collateral_symbol = "USDC"  # Changed to USDC to avoid ETH wrapping issue
    size_usd = 100
    leverage = 2.0

    console.print(f"Market: {market_symbol}")
    console.print(f"Collateral: {collateral_symbol}")
    console.print(f"Size: ${size_usd}")
    console.print(f"Leverage: {leverage}x")

    # Create orders (not executed, just for estimation)
    console.print("\n[bold yellow]━━━ Creating Orders (not executed) ━━━[/bold yellow]")

    try:
        # Open position order
        console.print("\n[bold green]1. Open Position Order[/bold green]")
        open_order = trading.open_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=collateral_symbol,
            is_long=True,
            size_delta_usd=size_usd,
            leverage=leverage,
            slippage_percent=0.003,
        )

        tx_value = open_order.transaction.get("value", 0)
        execution_fee = open_order.execution_fee
        collateral_eth = tx_value - execution_fee

        # Estimate blockchain gas cost with gas monitor
        try:
            estimate = gas_monitor.estimate_transaction_gas(
                open_order.transaction,
                wallet_address,
            )
            gas_cost_eth = estimate.estimated_cost_native
            gas_cost_usd = estimate.estimated_cost_usd
        except Exception as e:
            # If gas estimation fails (usually due to insufficient balance),
            # use rough estimates: ~450k gas for open position
            console.print(f"  [yellow]Gas estimation failed:[/yellow] {str(e)[:200]}")
            console.print("  [dim]Using rough estimate: ~450k gas[/dim]")
            estimated_gas = 450000
            gas_cost_wei = estimated_gas * gas_price
            gas_cost_eth = web3.from_wei(gas_cost_wei, "ether")
            gas_cost_usd = float(gas_cost_eth) * eth_price

        execution_fee_eth = execution_fee / 10**18

        # Collateral is in USDC, value is only execution fee
        if collateral_symbol == "USDC":
            collateral_usd = size_usd / leverage
            total_eth = (tx_value / 10**18) + float(gas_cost_eth)
            console.print(f"  Blockchain gas: {gas_cost_eth:.6f} ETH (${gas_cost_usd:.2f})")
            console.print(f"  Execution fee (keeper): {execution_fee_eth:.6f} ETH (${execution_fee_eth * eth_price:.2f})")
            console.print(f"  Collateral: ${collateral_usd:.2f} USDC")
            console.print(f"  [bold]Total ETH needed: {total_eth:.6f} ETH (${total_eth * eth_price:.2f})[/bold]")

            open_result = {
                "operation": "Open Position",
                "gas_cost_eth": float(gas_cost_eth),
                "gas_cost_usd": gas_cost_usd,
                "execution_fee_eth": execution_fee_eth,
                "execution_fee_usd": execution_fee_eth * eth_price,
                "collateral_eth": 0,
                "collateral_usd": collateral_usd,
                "total_eth": total_eth,
                "total_usd": total_eth * eth_price,
            }
        else:
            # ETH collateral
            collateral_amount_eth = collateral_eth / 10**18
            total_eth = (tx_value / 10**18) + float(gas_cost_eth)
            console.print(f"  Blockchain gas: {gas_cost_eth:.6f} ETH (${gas_cost_usd:.2f})")
            console.print(f"  Execution fee (keeper): {execution_fee_eth:.6f} ETH (${execution_fee_eth * eth_price:.2f})")
            console.print(f"  Collateral: {collateral_amount_eth:.6f} ETH (${collateral_amount_eth * eth_price:.2f})")
            console.print(f"  [bold]Total ETH needed: {total_eth:.6f} ETH (${total_eth * eth_price:.2f})[/bold]")

            open_result = {
                "operation": "Open Position",
                "gas_cost_eth": float(gas_cost_eth),
                "gas_cost_usd": gas_cost_usd,
                "execution_fee_eth": execution_fee_eth,
                "execution_fee_usd": execution_fee_eth * eth_price,
                "collateral_eth": collateral_amount_eth,
                "collateral_usd": collateral_amount_eth * eth_price,
                "total_eth": total_eth,
                "total_usd": total_eth * eth_price,
            }

    except Exception as e:
        console.print(f"  [red]Error: {e}[/red]")
        open_result = None

    try:
        # Close position order
        console.print("\n[bold red]2. Close Position Order[/bold red]")
        close_order = trading.close_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=collateral_symbol,
            is_long=True,
            size_delta_usd=size_usd,
            initial_collateral_delta=size_usd / leverage,
            slippage_percent=0.003,
        )

        tx_value = close_order.transaction.get("value", 0)
        execution_fee = close_order.execution_fee

        # Estimate blockchain gas cost with gas monitor
        try:
            estimate = gas_monitor.estimate_transaction_gas(
                close_order.transaction,
                wallet_address,
            )
            gas_cost_eth = estimate.estimated_cost_native
            gas_cost_usd = estimate.estimated_cost_usd
        except Exception as e:
            # If gas estimation fails, use rough estimates: ~380k gas for close position
            console.print(f"  [yellow]Gas estimation failed:[/yellow] {str(e)[:200]}")
            console.print("  [dim]Using rough estimate: ~380k gas[/dim]")
            estimated_gas = 380000
            gas_cost_wei = estimated_gas * gas_price
            gas_cost_eth = web3.from_wei(gas_cost_wei, "ether")
            gas_cost_usd = float(gas_cost_eth) * eth_price

        execution_fee_eth = execution_fee / 10**18
        total_eth = (tx_value / 10**18) + float(gas_cost_eth)

        console.print(f"  Blockchain gas: {gas_cost_eth:.6f} ETH (${gas_cost_usd:.2f})")
        console.print(f"  Execution fee (keeper): {execution_fee_eth:.6f} ETH (${execution_fee_eth * eth_price:.2f})")
        console.print(f"  [bold]Total ETH needed: {total_eth:.6f} ETH (${total_eth * eth_price:.2f})[/bold]")

        close_result = {
            "operation": "Close Position",
            "gas_cost_eth": float(gas_cost_eth),
            "gas_cost_usd": gas_cost_usd,
            "execution_fee_eth": execution_fee_eth,
            "execution_fee_usd": execution_fee_eth * eth_price,
            "collateral_eth": 0,
            "collateral_usd": 0,
            "total_eth": total_eth,
            "total_usd": total_eth * eth_price,
        }

    except Exception as e:
        console.print(f"  [red]Error: {e}[/red]")
        close_result = None

    # Summary table
    console.print("\n[bold cyan]━━━ Summary ━━━[/bold cyan]\n")

    table = Table(title="GMX Trading Costs Breakdown")
    table.add_column("Operation", style="cyan", width=14)
    table.add_column("Gas (ETH)", justify="right", style="blue")
    table.add_column("Gas (USD)", justify="right", style="blue")
    table.add_column("Keeper Fee", justify="right", style="yellow")
    table.add_column("Keeper USD", justify="right", style="yellow")
    table.add_column("Collateral", justify="right", style="white")
    table.add_column("Coll. USD", justify="right", style="white")
    table.add_column("Total ETH", justify="right", style="green")
    table.add_column("Total USD", justify="right", style="green")

    if open_result:
        # Display collateral based on type
        if open_result["collateral_eth"] > 0:
            collateral_display = f"{open_result['collateral_eth']:.6f}"
        else:
            collateral_display = f"${open_result['collateral_usd']:.2f}"

        table.add_row(
            open_result["operation"],
            f"{open_result['gas_cost_eth']:.6f}",
            f"${open_result['gas_cost_usd']:.2f}",
            f"{open_result['execution_fee_eth']:.6f}",
            f"${open_result['execution_fee_usd']:.2f}",
            collateral_display,
            f"${open_result['collateral_usd']:.2f}",
            f"{open_result['total_eth']:.6f}",
            f"${open_result['total_usd']:.2f}",
        )

    if close_result:
        table.add_row(
            close_result["operation"],
            f"{close_result['gas_cost_eth']:.6f}",
            f"${close_result['gas_cost_usd']:.2f}",
            f"{close_result['execution_fee_eth']:.6f}",
            f"${close_result['execution_fee_usd']:.2f}",
            "—",
            "—",
            f"{close_result['total_eth']:.6f}",
            f"${close_result['total_usd']:.2f}",
        )

    console.print(table)

    # Total costs
    if open_result and close_result:
        total_gas = open_result["gas_cost_eth"] + close_result["gas_cost_eth"]
        total_gas_usd = open_result["gas_cost_usd"] + close_result["gas_cost_usd"]
        total_execution_fees = open_result["execution_fee_eth"] + close_result["execution_fee_eth"]
        total_execution_fees_usd = open_result["execution_fee_usd"] + close_result["execution_fee_usd"]
        total_collateral = open_result["collateral_eth"]
        total_collateral_usd = open_result["collateral_usd"]
        grand_total = open_result["total_eth"] + close_result["total_eth"]
        grand_total_usd = open_result["total_usd"] + close_result["total_usd"]

        console.print("\n[bold]Round Trip (Open + Close):[/bold]")
        console.print(f"  Blockchain gas: {total_gas:.6f} ETH (${total_gas_usd:.2f})")
        console.print(f"  Keeper fees: {total_execution_fees:.6f} ETH (${total_execution_fees_usd:.2f})")

        # Display collateral based on type
        if total_collateral > 0:
            console.print(f"  Collateral locked: {total_collateral:.6f} ETH (${total_collateral_usd:.2f})")
        else:
            console.print(f"  Collateral locked: ${total_collateral_usd:.2f} USDC")

        console.print(f"  [bold]Total ETH: {grand_total:.6f} ETH (${grand_total_usd:.2f})[/bold]")

        # Check if user has enough
        console.print()
        if gas_check.native_balance < grand_total:
            console.print(f"[bold red]⚠ Insufficient balance![/bold red]")
            console.print(f"You need {grand_total:.6f} ETH but have {gas_check.native_balance:.6f} ETH")
            console.print(f"Shortfall: {grand_total - float(gas_check.native_balance):.6f} ETH")
        else:
            console.print(f"[bold green]✓ You have sufficient balance[/bold green]")

    console.print("\n[bold]Cost Breakdown:[/bold]")
    console.print("[dim]• Blockchain Gas:[/dim] Transaction fee paid to Arbitrum validators")
    console.print("[dim]• Keeper Fee:[/dim] Paid to GMX keepers who execute your orders")
    console.print(f"[dim]• Collateral:[/dim] ${size_usd / leverage:.2f} in {collateral_symbol}, locked in position, returned when you close")
    console.print("\n[dim]Note: No transactions were sent. These are estimates only.[/dim]\n")


if __name__ == "__main__":
    main()

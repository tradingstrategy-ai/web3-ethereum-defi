"""Fetch GMX positions using the GraphQL Subsquid client.

This script demonstrates how to use the GMXSubsquidClient to fetch position data
from the Subsquid indexer, which is faster than direct contract calls and provides
historical analytics.

Example:
    python scripts/gmx/gmx_graphql_positions.py

The script fetches positions for a given account and displays:
- Open positions with PnL
- PnL summary across time periods
- Position change history
- Overall account statistics
"""

from rich.console import Console
from rich.table import Table
from eth_defi.gmx.graphql.client import GMXSubsquidClient

console = Console()


def display_positions(client: GMXSubsquidClient, positions: list):
    """Display positions in a formatted table."""
    if not positions:
        console.print("[yellow]No positions found[/yellow]")
        return

    table = Table(title="Open Positions", show_header=True, header_style="bold magenta")
    table.add_column("Market", style="cyan")
    table.add_column("Direction", style="magenta")
    table.add_column("Size (USD)", justify="right", style="green")
    table.add_column("Collateral", justify="right", style="blue")
    table.add_column("Entry Price", justify="right")
    table.add_column("Leverage", justify="right", style="yellow")
    table.add_column("Unrealized PnL", justify="right")

    for pos in positions:
        formatted = client.format_position(pos)

        # Color code PnL
        pnl = formatted["unrealized_pnl"]
        pnl_color = "green" if pnl >= 0 else "red"
        pnl_str = f"[{pnl_color}]${pnl:,.2f}[/{pnl_color}]"

        table.add_row(
            formatted["market"][:10] + "...",
            "LONG" if formatted["is_long"] else "SHORT",
            f"${formatted['size_usd']:,.2f}",
            f"${formatted['collateral_amount']:,.2f}",
            f"${formatted['entry_price']:,.2f}",
            f"{formatted['leverage']:.2f}x",
            pnl_str,
        )

    console.print(table)


def display_pnl_summary(pnl_data: list):
    """Display PnL summary in a formatted table."""
    if not pnl_data:
        console.print("[yellow]No PnL data found[/yellow]")
        return

    table = Table(title="PnL Summary", show_header=True, header_style="bold magenta")
    table.add_column("Period", style="cyan")
    table.add_column("Total PnL", justify="right", style="green")
    table.add_column("Realized", justify="right")
    table.add_column("Unrealized", justify="right")
    table.add_column("Volume", justify="right", style="blue")
    table.add_column("Wins", justify="right", style="green")
    table.add_column("Losses", justify="right", style="red")
    table.add_column("Win Rate", justify="right")

    for stat in pnl_data:
        total_pnl = float(GMXSubsquidClient.from_fixed_point(stat["pnlUsd"]))
        realized_pnl = float(GMXSubsquidClient.from_fixed_point(stat["realizedPnlUsd"]))
        unrealized_pnl = float(GMXSubsquidClient.from_fixed_point(stat["unrealizedPnlUsd"]))
        volume = float(GMXSubsquidClient.from_fixed_point(stat["volume"]))

        wins = stat["wins"]
        losses = stat["losses"]
        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        # Color code total PnL
        pnl_color = "green" if total_pnl >= 0 else "red"

        table.add_row(
            stat["bucketLabel"].upper(),
            f"[{pnl_color}]${total_pnl:,.2f}[/{pnl_color}]",
            f"${realized_pnl:,.2f}",
            f"${unrealized_pnl:,.2f}",
            f"${volume:,.0f}",
            str(wins),
            str(losses),
            f"{win_rate:.1f}%",
        )

    console.print(table)


def display_account_stats(stats: dict):
    """Display account statistics."""
    if not stats:
        console.print("[yellow]No account stats found[/yellow]")
        return

    console.print("\n[bold cyan]Account Statistics[/bold cyan]")
    console.print(f"  Account ID: {stats['id']}")
    console.print(
        f"  Total Volume: ${float(GMXSubsquidClient.from_fixed_point(stats['volume'])):,.2f}",
    )
    console.print(f"  Closed Positions: {stats['closedCount']}")
    console.print(
        f"  Realized PnL: ${float(GMXSubsquidClient.from_fixed_point(stats['realizedPnl'])):,.2f}",
    )
    console.print(
        f"  Realized Fees: ${float(GMXSubsquidClient.from_fixed_point(stats['realizedFees'])):,.2f}",
    )


if __name__ == "__main__":
    console.print("[bold cyan]GMX GraphQL Positions Fetcher[/bold cyan]\n")

    client = GMXSubsquidClient(chain="arbitrum")

    # Example account with open positions
    # Replace this with any account address you want to query
    account = "0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c"

    console.print(f"[bold]Querying data for account:[/bold] {account}\n")

    # 1. Fetch and display open positions
    console.print("[bold cyan]1. Open Positions[/bold cyan]")
    try:
        positions = client.get_positions(account=account, only_open=True, limit=100)
        display_positions(client, positions)
    except Exception as e:
        console.print(f"[red]Error fetching positions: {e}[/red]")

    console.print()

    # 2. Fetch and display PnL summary
    console.print("[bold cyan]2. PnL Summary[/bold cyan]")
    try:
        pnl_summary = client.get_pnl_summary(account=account)
        display_pnl_summary(pnl_summary)
    except Exception as e:
        console.print(f"[red]Error fetching PnL summary: {e}[/red]")

    console.print()

    # 3. Fetch and display account statistics
    console.print("[bold cyan]3. Account Statistics[/bold cyan]")
    try:
        stats = client.get_account_stats(account=account)
        display_account_stats(stats)
    except Exception as e:
        console.print(f"[red]Error fetching account stats: {e}[/red]")

    console.print("\n[bold green]Done![/bold green]")

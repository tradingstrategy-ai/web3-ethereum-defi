"""Example usage of GMX Subsquid GraphQL client.

This demonstrates how to use the GraphQL client to fetch position and PnL data
from the Subsquid indexer, which is faster than direct contract calls and provides
historical analytics.
"""

from eth_defi.gmx.graphql.client import GMXSubsquidClient
from rich.console import Console
from rich.table import Table

console = Console()


def display_positions(positions):
    """Display positions in a formatted table."""
    if not positions:
        console.print("[yellow]No positions found[/yellow]")
        return

    table = Table(title="Open Positions")
    table.add_column("Market", style="cyan")
    table.add_column("Direction", style="magenta")
    table.add_column("Size (USD)", justify="right", style="green")
    table.add_column("Collateral", justify="right", style="blue")
    table.add_column("Entry Price", justify="right")
    table.add_column("Leverage", justify="right", style="yellow")
    table.add_column("Unrealized PnL", justify="right")

    for pos in positions:
        formatted = GMXSubsquidClient.format_position(pos)

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


def display_pnl_summary(pnl_data):
    """Display PnL summary in a formatted table."""
    if not pnl_data:
        console.print("[yellow]No PnL data found[/yellow]")
        return

    table = Table(title="PnL Summary")
    table.add_column("Period", style="cyan")
    table.add_column("Total PnL", justify="right", style="green")
    table.add_column("Realized", justify="right")
    table.add_column("Unrealized", justify="right")
    table.add_column("Volume", justify="right", style="blue")
    table.add_column("Wins", justify="right", style="green")
    table.add_column("Losses", justify="right", style="red")
    table.add_column("Win Rate", justify="right")

    for stat in pnl_data:
        total_pnl = float(GMXSubsquidClient.parse_bigint(stat["pnlUsd"]))
        realized_pnl = float(GMXSubsquidClient.parse_bigint(stat["realizedPnlUsd"]))
        unrealized_pnl = float(GMXSubsquidClient.parse_bigint(stat["unrealizedPnlUsd"]))
        volume = float(GMXSubsquidClient.parse_bigint(stat["volume"]))

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


def display_position_changes(changes):
    """Display position change history in a formatted table."""
    if not changes:
        console.print("[yellow]No position changes found[/yellow]")
        return

    table = Table(title="Position Change History")
    table.add_column("ID", style="dim")
    table.add_column("Market", style="magenta")
    table.add_column("Collateral Token", style="cyan")
    table.add_column("Direction")
    table.add_column("Size (USD)", justify="right", style="yellow")
    table.add_column("Collateral", justify="right", style="blue")

    for change in changes:
        size_usd = float(GMXSubsquidClient.parse_bigint(change["sizeInUsd"]))
        collateral = float(GMXSubsquidClient.parse_bigint(change["collateralAmount"]))

        table.add_row(
            change["id"][:15] + "...",
            change["market"][:10] + "...",
            change["collateralToken"][:10] + "...",
            "LONG" if change["isLong"] else "SHORT",
            f"${size_usd:,.2f}",
            f"${collateral:,.4f}",
        )

    console.print(table)


def main():
    """Demonstrate GraphQL client usage."""
    console.print("[bold cyan]GMX Subsquid GraphQL Client Demo[/bold cyan]\n")

    # Initialize client
    client = GMXSubsquidClient(chain="arbitrum")

    # Example account with open positions
    test_account = "0x6fa415E36Ac2a20499956C1CCe8a361a3E419a4D"

    console.print(f"[bold]Querying data for account:[/bold] {test_account}\n")

    # 1. Get open positions
    console.print("[bold cyan]1. Fetching Open Positions...[/bold cyan]")
    try:
        positions = client.get_positions(account=test_account, only_open=True)
        display_positions(positions)
    except Exception as e:
        console.print(f"[red]Error fetching positions: {e}[/red]")

    console.print()

    # 2. Get PnL summary
    console.print("[bold cyan]2. Fetching PnL Summary...[/bold cyan]")
    try:
        pnl_summary = client.get_pnl_summary(account=test_account)
        display_pnl_summary(pnl_summary)
    except Exception as e:
        console.print(f"[red]Error fetching PnL summary: {e}[/red]")

    console.print()

    # 3. Get position changes
    console.print("[bold cyan]3. Fetching Position Change History...[/bold cyan]")
    try:
        changes = client.get_position_changes(account=test_account, limit=10)
        display_position_changes(changes)
    except Exception as e:
        console.print(f"[red]Error fetching position changes: {e}[/red]")

    console.print()

    # 4. Get account stats
    console.print("[bold cyan]4. Fetching Account Statistics...[/bold cyan]")
    try:
        stats = client.get_account_stats(account=test_account)
        if stats:
            console.print(f"  Account ID: {stats['id']}")
            console.print(f"  Total Volume: ${float(GMXSubsquidClient.parse_bigint(stats['volume'])):,.2f}")
            console.print(f"  Closed Positions: {stats['closedCount']}")
            console.print(f"  Realized PnL: ${float(GMXSubsquidClient.parse_bigint(stats['realizedPnl'])):,.2f}")
            console.print(f"  Realized Fees: ${float(GMXSubsquidClient.parse_bigint(stats['realizedFees'])):,.2f}")
        else:
            console.print("[yellow]No account stats found[/yellow]")
    except Exception as e:
        console.print(f"[red]Error fetching account stats: {e}[/red]")

    console.print("\n[bold green]Demo complete![/bold green]")


if __name__ == "__main__":
    main()

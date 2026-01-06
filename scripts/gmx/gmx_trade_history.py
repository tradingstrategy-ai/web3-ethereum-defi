"""GMX trade history viewer.

Fetches and decodes GMX trading events for a given address, showing
execution details for successful trades and error reasons for failed ones.

Usage:
    export JSON_RPC_ARBITRUM="https://your-rpc-url"
    export TRADER_ADDRESS="0x962b94eBB41a7fbd936a47d0dB34502a66DF5f62"
    export TRADE_LIMIT="30"
    export MAX_WORKERS="10"
    poetry run python scripts/gmx/gmx_trade_history.py
"""

import logging
import os
from datetime import datetime

from joblib import Parallel, delayed
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.text import Text
from web3 import Web3

from eth_defi.gmx.events import (
    GMX_PRICE_PRECISION,
    GMX_USD_PRECISION,
    decode_gmx_events,
    extract_order_execution_result,
)

# Set up logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Rich console for output
console = Console()

# GMX EventEmitter contract address on Arbitrum
GMX_EVENT_EMITTER_ARBITRUM = "0xC8ee91A54287DB53897056e12D9819156D3822Fb"


def fetch_gmx_events_for_address(
    web3: Web3,
    trader_address: str,
    from_block: int | None = None,
    limit: int = 30,
) -> list[str]:
    """Fetch GMX event transaction hashes for a trader address.

    Uses eth_getLogs to find transactions where this address appears
    in GMX events (topic2 contains the account address for EventLog2).

    :param web3:
        Web3 instance

    :param trader_address:
        Trader address to find events for

    :param from_block:
        Starting block (defaults to recent blocks)

    :param limit:
        Maximum number of transactions to fetch

    :return:
        List of unique transaction hashes
    """
    latest_block = web3.eth.block_number

    # Search last ~7 days of blocks (Arbitrum ~4 blocks/second)
    if from_block is None:
        from_block = max(0, latest_block - (4 * 60 * 60 * 24 * 7))

    # Pad address to 32 bytes for topic filtering
    padded_address = "0x" + trader_address.lower()[2:].zfill(64)

    tx_hashes = set()

    # Query in chunks to avoid RPC limits
    chunk_size = 50000
    current_block = latest_block

    console.print(f"[dim]Searching blocks {from_block:,} to {latest_block:,}...[/dim]")

    while current_block > from_block and len(tx_hashes) < limit * 2:
        start_block = max(from_block, current_block - chunk_size)

        try:
            # EventLog2 has account in topic2
            logs = web3.eth.get_logs(
                {
                    "address": GMX_EVENT_EMITTER_ARBITRUM,
                    "fromBlock": start_block,
                    "toBlock": current_block,
                    "topics": [
                        None,  # Any event name hash
                        None,  # topic1 (order key)
                        padded_address,  # topic2 (account address)
                    ],
                }
            )

            for log in logs:
                tx_hashes.add(log["transactionHash"].hex())

        except Exception as e:
            logger.debug("Log query failed for blocks %s-%s: %s", start_block, current_block, e)

        current_block = start_block - 1

        if len(tx_hashes) >= limit * 2:
            break

    return list(tx_hashes)[: limit * 2]


def format_usd(value: float | None, precision: int = 4) -> str:
    """Format USD value with colour coding."""
    if value is None:
        return "-"

    if abs(value) < 0.0001:
        return "$0.00"

    formatted = f"${value:,.{precision}f}"

    return formatted


def format_pnl(value: float | None) -> Text:
    """Format PnL with colour coding."""
    if value is None:
        return Text("-", style="dim")

    if abs(value) < 0.0001:
        return Text("$0.00", style="dim")

    if value > 0:
        return Text(f"+${value:,.4f}", style="green")
    else:
        return Text(f"-${abs(value):,.4f}", style="red")


def analyse_transaction_worker(
    rpc_url: str,
    tx_hash: str,
    trader_address: str,
) -> dict | None:
    """Worker function for parallel transaction analysis.

    Creates its own Web3 connection for thread safety.

    :param rpc_url:
        RPC URL to connect to

    :param tx_hash:
        Transaction hash to analyse

    :param trader_address:
        Trader address to filter events for

    :return:
        Dictionary with trade data, or None if not a relevant trade
    """
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    return analyse_transaction(web3, tx_hash, trader_address)


def analyse_transaction(
    web3: Web3,
    tx_hash: str,
    trader_address: str,
) -> dict | None:
    """Analyse a single transaction for GMX trading events.

    :param web3:
        Web3 instance

    :param tx_hash:
        Transaction hash to analyse

    :param trader_address:
        Trader address to filter events for

    :return:
        Dictionary with trade data, or None if not a relevant trade
    """
    try:
        receipt = web3.eth.get_transaction_receipt(tx_hash)
    except Exception as e:
        logger.debug("Failed to get receipt for %s: %s", tx_hash, e)
        return None

    # Decode GMX events
    events = list(decode_gmx_events(web3, receipt))

    if not events:
        return None

    # Check if this transaction involves our trader
    trader_lower = trader_address.lower()
    relevant = False

    for event in events:
        account = event.get_address("account")
        if account and account.lower() == trader_lower:
            relevant = True
            break

    if not relevant:
        return None

    # Get order execution result
    result = extract_order_execution_result(web3, receipt)

    if not result:
        return None

    # Check if the account matches our trader
    if result.account and result.account.lower() != trader_lower:
        return None

    # Get block timestamp
    block = web3.eth.get_block(receipt["blockNumber"])
    timestamp = datetime.utcfromtimestamp(block["timestamp"])

    # Build trade data
    trade_data = {
        "tx_hash": tx_hash,
        "block_number": receipt["blockNumber"],
        "timestamp": timestamp,
        "status": result.status,
        "order_key": result.order_key.hex()[:16] if result.order_key else None,
        "event_count": len(events),
        "event_names": [e.event_name for e in events],
    }

    if result.status == "executed":
        # Successful trade
        trade_data["success"] = True

        # Get position event for more details
        position_events = [e for e in events if e.event_name == "PositionIncrease"]
        if not position_events:
            position_events = [e for e in events if e.event_name == "PositionDecrease"]

        if position_events:
            pos_event = position_events[0]
            trade_data["is_long"] = pos_event.get_bool("isLong")
            trade_data["market"] = pos_event.get_address("market")

            # Get collateral token
            collateral_token = pos_event.get_address("collateralToken")
            trade_data["collateral_token"] = collateral_token

        # Execution details
        if result.execution_price:
            trade_data["execution_price"] = result.execution_price / GMX_PRICE_PRECISION

        if result.size_delta_usd:
            trade_data["size_delta_usd"] = result.size_delta_usd / GMX_USD_PRECISION

        if result.pnl_usd is not None:
            trade_data["pnl_usd"] = result.pnl_usd / GMX_USD_PRECISION

        if result.price_impact_usd is not None:
            trade_data["price_impact_usd"] = result.price_impact_usd / GMX_USD_PRECISION

        if result.collateral_delta is not None:
            # Collateral delta is in token decimals (usually 6 for USDC)
            trade_data["collateral_delta"] = result.collateral_delta

        # Fees
        if result.fees:
            trade_data["fees"] = {
                "position_fee": result.fees.position_fee,
                "borrowing_fee": result.fees.borrowing_fee,
                "funding_fee": result.fees.funding_fee,
            }

        trade_data["is_long"] = result.is_long

    else:
        # Failed trade (cancelled or frozen)
        trade_data["success"] = False
        trade_data["reason"] = result.reason
        trade_data["decoded_error"] = result.decoded_error

        if result.reason_bytes and len(result.reason_bytes) >= 4:
            trade_data["error_selector"] = f"0x{result.reason_bytes[:4].hex()}"

    return trade_data


def display_trades(trades: list[dict], trader_address: str):
    """Display trades using rich tables.

    :param trades:
        List of trade data dictionaries

    :param trader_address:
        Trader address for display
    """
    # Summary panel
    successful = sum(1 for t in trades if t.get("success"))
    failed = len(trades) - successful

    summary = Table.grid(padding=1)
    summary.add_column(style="cyan", justify="right")
    summary.add_column()
    summary.add_row("Address:", trader_address)
    summary.add_row("Total trades:", str(len(trades)))
    summary.add_row("Successful:", f"[green]{successful}[/green]")
    summary.add_row("Failed:", f"[red]{failed}[/red]" if failed > 0 else "[dim]0[/dim]")

    console.print(Panel(summary, title="GMX Trade History", border_style="blue"))
    console.print()

    # Successful trades table
    if successful > 0:
        success_table = Table(
            title="[green]Successful Trades[/green]",
            show_header=True,
            header_style="bold",
            border_style="green",
        )
        success_table.add_column("Time", style="dim", width=19)
        success_table.add_column("Direction", width=10)
        success_table.add_column("Size", justify="right", width=12)
        success_table.add_column("Price", justify="right", width=12)
        success_table.add_column("PnL", justify="right", width=12)
        success_table.add_column("Impact", justify="right", width=10)
        success_table.add_column("Tx Hash", width=18)

        for trade in trades:
            if not trade.get("success"):
                continue

            # Direction
            if trade.get("is_long") is True:
                direction = Text("LONG", style="green")
            elif trade.get("is_long") is False:
                direction = Text("SHORT", style="red")
            else:
                direction = Text("-", style="dim")

            # Determine if increase or decrease
            event_names = trade.get("event_names", [])
            if "PositionDecrease" in event_names:
                direction = Text(f"Close {direction.plain}", style=direction.style)

            success_table.add_row(
                trade["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                direction,
                format_usd(trade.get("size_delta_usd"), 2),
                format_usd(trade.get("execution_price"), 2),
                format_pnl(trade.get("pnl_usd")),
                format_usd(trade.get("price_impact_usd"), 4),
                f"[link=https://arbiscan.io/tx/{trade['tx_hash']}]{trade['tx_hash'][:10]}...[/link]",
            )

        console.print(success_table)
        console.print()

    # Failed trades table
    if failed > 0:
        fail_table = Table(
            title="[red]Failed Trades[/red]",
            show_header=True,
            header_style="bold",
            border_style="red",
        )
        fail_table.add_column("Time", style="dim", width=19)
        fail_table.add_column("Status", width=10)
        fail_table.add_column("Error", width=50)
        fail_table.add_column("Selector", width=12)
        fail_table.add_column("Tx Hash", width=18)

        for trade in trades:
            if trade.get("success"):
                continue

            status = Text(trade["status"].upper(), style="red")
            error = trade.get("decoded_error") or trade.get("reason") or "-"

            fail_table.add_row(
                trade["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                status,
                error,
                trade.get("error_selector", "-"),
                f"[link=https://arbiscan.io/tx/{trade['tx_hash']}]{trade['tx_hash'][:10]}...[/link]",
            )

        console.print(fail_table)
        console.print()

    # Detailed view for each trade
    console.print("[bold]Detailed Trade Information[/bold]")
    console.print()

    for i, trade in enumerate(trades, 1):
        if trade.get("success"):
            style = "green"
            title = f"Trade #{i} - [green]SUCCESS[/green]"
        else:
            style = "red"
            title = f"Trade #{i} - [red]{trade['status'].upper()}[/red]"

        detail_table = Table.grid(padding=(0, 2))
        detail_table.add_column(style="cyan", justify="right", width=18)
        detail_table.add_column()

        detail_table.add_row("Transaction:", f"[link=https://arbiscan.io/tx/{trade['tx_hash']}]{trade['tx_hash']}[/link]")
        detail_table.add_row("Block:", str(trade["block_number"]))
        detail_table.add_row("Time:", trade["timestamp"].strftime("%Y-%m-%d %H:%M:%S UTC"))
        detail_table.add_row("Order Key:", trade.get("order_key", "-") + "...")
        detail_table.add_row("Events:", str(trade["event_count"]))

        if trade.get("success"):
            # Success details
            direction = "LONG" if trade.get("is_long") else "SHORT" if trade.get("is_long") is False else "-"
            detail_table.add_row("Direction:", direction)
            detail_table.add_row("Execution Price:", format_usd(trade.get("execution_price"), 2))
            detail_table.add_row("Size Delta:", format_usd(trade.get("size_delta_usd"), 4))

            pnl = trade.get("pnl_usd")
            if pnl is not None:
                pnl_text = format_pnl(pnl)
                detail_table.add_row("Realised PnL:", pnl_text)

            detail_table.add_row("Price Impact:", format_usd(trade.get("price_impact_usd"), 6))

            if trade.get("fees"):
                fees = trade["fees"]
                fee_parts = []
                if fees.get("position_fee"):
                    fee_parts.append(f"pos: {fees['position_fee']}")
                if fees.get("borrowing_fee"):
                    fee_parts.append(f"borrow: {fees['borrowing_fee']}")
                if fees.get("funding_fee"):
                    fee_parts.append(f"funding: {fees['funding_fee']}")
                detail_table.add_row("Fees (raw):", ", ".join(fee_parts) if fee_parts else "-")

        else:
            # Failure details
            detail_table.add_row("Error:", trade.get("decoded_error") or trade.get("reason") or "-")
            detail_table.add_row("Selector:", trade.get("error_selector", "-"))

        console.print(Panel(detail_table, title=title, border_style=style))
        console.print()


def main():
    """Main entry point."""
    # Get configuration from environment
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM", "https://arb1.arbitrum.io/rpc")
    # Handle space-separated fallback URLs
    rpc_url = rpc_url.split()[0]

    trader_address = os.environ.get("TRADER_ADDRESS", "0x962b94eBB41a7fbd936a47d0dB34502a66DF5f62")
    trade_limit = int(os.environ.get("TRADE_LIMIT", "30"))

    # Connect to Arbitrum
    web3 = Web3(Web3.HTTPProvider(rpc_url))

    if not web3.is_connected():
        console.print("[red]Failed to connect to Arbitrum RPC[/red]")
        return

    console.print(f"[dim]Connected to chain ID: {web3.eth.chain_id}[/dim]")
    console.print(f"[dim]Fetching trades for: {trader_address}[/dim]")
    console.print()

    # Fetch transactions directly from blockchain logs
    with console.status("[bold]Fetching GMX events from blockchain...[/bold]"):
        tx_hashes = fetch_gmx_events_for_address(
            web3,
            trader_address,
            limit=trade_limit,
        )

    console.print(f"[dim]Found {len(tx_hashes)} transactions with GMX events[/dim]")
    console.print()

    # Get max workers from environment
    max_workers = int(os.environ.get("MAX_WORKERS", "10"))

    # Analyse transactions in parallel using joblib
    console.print(f"[dim]Analysing transactions with {max_workers} workers...[/dim]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Analysing trades...", total=len(tx_hashes))

        # Use joblib for parallel processing with threading backend
        results = Parallel(n_jobs=max_workers, backend="threading")(delayed(analyse_transaction_worker)(rpc_url, tx_hash, trader_address) for tx_hash in tx_hashes)

        progress.update(task, completed=len(tx_hashes))

    # Filter out None results and limit
    trades = [r for r in results if r is not None][:trade_limit]

    # Sort by timestamp (most recent first)
    trades.sort(key=lambda t: t["timestamp"], reverse=True)

    if not trades:
        console.print("[yellow]No GMX trades found for this address[/yellow]")
        return

    console.print(f"[dim]Found {len(trades)} GMX trades[/dim]")
    console.print()

    # Display results
    display_trades(trades, trader_address)


if __name__ == "__main__":
    main()

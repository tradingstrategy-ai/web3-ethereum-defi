"""Cancel pending GMX SL/TP orders using a freqtrade secrets config file.

Fetches all pending cancellable orders (stop loss, take profit, limit entry)
from the GMX DataStore for the configured wallet and offers an interactive
prompt to cancel them in a single batch transaction.

Usage::

    python scripts/cancel_gmx_orders.py configs/ichiv2_ls_gmx_static.secrets.json

The secrets file must contain::

    {
        "exchange": {
            "walletAddress": "0x...",   // optional – derived from privateKey if absent
            "ccxt_config": {
                "rpcUrl": "https://...",
                "privateKey": "0x..."
            }
        }
    }

The script supports ``// line comments`` inside the JSON file, matching the
freqtrade secrets format.
"""

import json
import logging
import re
import sys
from pathlib import Path

from eth_account import Account

try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError as _rich_err:
    print("Error: 'rich' is required by this script. Install it with: pip install rich")
    raise SystemExit(1) from _rich_err

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.order.cancel_order import CancelOrder
from eth_defi.gmx.order.pending_orders import PendingOrder, fetch_pending_orders
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
)
logger = logging.getLogger(__name__)

# Order type colour coding
_ORDER_TYPE_STYLE: dict[str, str] = {
    "STOP_LOSS_DECREASE": "bold red",
    "LIMIT_DECREASE": "bold green",
    "LIMIT_INCREASE": "bold cyan",
}


def _strip_json_comments(text: str) -> str:
    """Strip ``// line comments`` from a JSON-with-comments string.

    Only strips lines where ``//`` appears at the start (after optional
    whitespace), which is the freqtrade comment style.  This avoids
    accidentally stripping ``//`` inside URL values such as
    ``"rpcUrl": "https://arb1.arbitrum.io/rpc"``.

    .. note::
        Inline trailing comments (e.g. ``"key": "value",  // comment``) are
        **not** handled — only full-line comments are stripped.

    :param text: Raw JSON text that may contain ``//`` line comments.
    :return: Clean JSON string with comments removed.
    """
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


def _load_secrets(path: Path) -> dict:
    """Load a freqtrade secrets JSON file, stripping ``//`` comments.

    :param path: Path to the secrets JSON file.
    :return: Parsed secrets dictionary.
    :raises SystemExit: If the file cannot be parsed.
    """
    raw = path.read_text()
    clean = _strip_json_comments(raw)
    try:
        return json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse secrets file %s: %s", path, exc)
        sys.exit(1)


def _build_orders_table(orders: list[PendingOrder]) -> Table:
    """Build a Rich table displaying pending orders.

    :param orders: Pending orders to display.
    :return: Configured Rich Table ready to print.
    """
    table = Table(
        box=box.ROUNDED,
        border_style="bright_black",
        header_style="bold white",
        show_lines=False,
        title="[bold]Pending GMX Orders[/bold]",
        title_style="bright_white",
    )

    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Type", min_width=20)
    table.add_column("Order Key", style="dim cyan")
    table.add_column("Trigger Price", justify="right", style="yellow")
    table.add_column("Size USD", justify="right", style="white")
    table.add_column("Dir", justify="center")
    table.add_column("Status", justify="center")

    for i, order in enumerate(orders, start=1):
        type_style = _ORDER_TYPE_STYLE.get(order.order_type.name, "white")
        direction_text = Text("▲ long", style="green") if order.is_long else Text("▼ short", style="red")
        status_text = Text("frozen", style="yellow bold") if order.is_frozen else Text("active", style="bright_green")

        table.add_row(
            str(i),
            Text(order.order_type.name, style=type_style),
            f"0x{order.order_key.hex()[:18]}…",
            f"${order.trigger_price_usd:,.4f}",
            f"${order.size_delta_usd_human:,.2f}",
            direction_text,
            status_text,
        )

    return table


def _prompt_selection(orders: list[PendingOrder]) -> list[PendingOrder]:
    """Interactively prompt the user to select orders for cancellation.

    :param orders: All pending orders fetched from GMX.
    :return: Subset of orders selected for cancellation.
    """
    console.print()
    console.print(
        Panel(
            "[dim]  [bold white]all[/bold white]       – cancel every order\n  [bold white]sl[/bold white]        – cancel [bold red]stop-loss[/bold red] orders only\n  [bold white]tp[/bold white]        – cancel [bold green]take-profit[/bold green] orders only\n  [bold white]1,2,3[/bold white]     – cancel specific orders by number\n  [bold white]q[/bold white] / Enter – quit without cancelling[/dim]",
            title="[bold]Select orders to cancel[/bold]",
            border_style="bright_black",
            expand=False,
        )
    )
    console.print()

    choice = Prompt.ask("[bold cyan]Your choice[/bold cyan]", default="q").strip().lower()

    if not choice or choice in ("q", "quit"):
        return []

    if choice == "all":
        return orders

    if choice == "sl":
        selected = [o for o in orders if o.is_stop_loss]
        if not selected:
            console.print("[yellow]No stop-loss orders in the list.[/yellow]")
        return selected

    if choice == "tp":
        selected = [o for o in orders if o.is_take_profit]
        if not selected:
            console.print("[yellow]No take-profit orders in the list.[/yellow]")
        return selected

    # Comma-separated indices
    try:
        indices = [int(x.strip()) - 1 for x in choice.split(",")]
        return [orders[i] for i in indices]
    except (ValueError, IndexError) as exc:
        console.print(f"[bold red]Invalid selection '{choice}': {exc}[/bold red]")
        sys.exit(1)


def main() -> None:
    """Entry point: parse config, fetch orders, cancel selected orders."""
    if len(sys.argv) < 2:
        console.print(f"[bold red]Usage:[/bold red] {sys.argv[0]} <secrets-config.json>")
        sys.exit(1)

    secrets_path = Path(sys.argv[1])
    if not secrets_path.exists():
        console.print(f"[bold red]Error:[/bold red] Secrets file not found: {secrets_path}")
        sys.exit(1)

    secrets = _load_secrets(secrets_path)

    exchange_cfg: dict = secrets.get("exchange", {})
    ccxt_cfg: dict = exchange_cfg.get("ccxt_config", {})

    rpc_url: str = ccxt_cfg.get("rpcUrl", "")
    private_key: str = ccxt_cfg.get("privateKey", "")
    wallet_address: str = exchange_cfg.get("walletAddress", "")

    if not rpc_url:
        console.print("[bold red]Error:[/bold red] rpcUrl not found under exchange.ccxt_config.rpcUrl")
        sys.exit(1)
    if not private_key:
        console.print("[bold red]Error:[/bold red] privateKey not found under exchange.ccxt_config.privateKey")
        sys.exit(1)

    acct = Account.from_key(private_key)

    if not wallet_address:
        wallet_address = acct.address
        logger.info("Derived wallet address from private key: %s", wallet_address)

    logger.info("Connecting to RPC…")

    web3 = create_multi_provider_web3(rpc_url)
    hot_wallet = HotWallet(acct)
    hot_wallet.sync_nonce(web3)
    config = GMXConfig(web3=web3, user_wallet_address=wallet_address, wallet=hot_wallet)
    chain = config.get_chain()

    console.print(
        Panel(
            f"[bold]Chain:[/bold]   [cyan]{chain}[/cyan] (chain_id=[cyan]{web3.eth.chain_id}[/cyan])\n[bold]Wallet:[/bold]  [cyan]{wallet_address}[/cyan]",
            title="[bold]Connection[/bold]",
            border_style="blue",
            expand=False,
        )
    )

    # Fetch pending orders from GMX DataStore
    logger.info("Fetching pending orders from GMX DataStore…")
    orders: list[PendingOrder] = list(fetch_pending_orders(web3, chain, wallet_address))

    if not orders:
        console.print("\n[yellow]No pending cancellable orders found for this wallet.[/yellow]")
        return

    console.print()
    console.print(_build_orders_table(orders))

    # Interactive selection
    selected = _prompt_selection(orders)

    if not selected:
        console.print("[dim]No orders selected. Exiting.[/dim]")
        return

    # Confirm
    console.print()
    confirm_table = Table(box=box.SIMPLE, show_header=False, border_style="bright_black")
    confirm_table.add_column("Type", style="bold")
    confirm_table.add_column("Key", style="dim cyan")
    confirm_table.add_column("Trigger", style="yellow", justify="right")
    for o in selected:
        type_style = _ORDER_TYPE_STYLE.get(o.order_type.name, "white")
        confirm_table.add_row(
            Text(o.order_type.name, style=type_style),
            f"0x{o.order_key.hex()[:18]}…",
            f"${o.trigger_price_usd:,.4f}",
        )

    console.print(
        Panel(
            confirm_table,
            title=f"[bold]Cancelling {len(selected)} order(s)[/bold]",
            border_style="yellow",
            expand=False,
        )
    )

    confirm = Prompt.ask("[bold yellow]Proceed?[/bold yellow] [dim]\[y/N][/dim]", default="n").strip().lower()
    if confirm not in ("y", "yes"):
        console.print("[dim]Aborted.[/dim]")
        return

    # Build and send cancel transaction
    order_keys = [o.order_key for o in selected]
    canceller = CancelOrder(config)

    if len(order_keys) == 1:
        result = canceller.cancel_order(order_keys[0])
    else:
        result = canceller.cancel_orders(order_keys)

    tx = result.transaction.copy()
    del tx["nonce"]

    signed = hot_wallet.sign_transaction_with_new_nonce(tx)

    with console.status("[bold cyan]Broadcasting transaction…[/bold cyan]"):
        tx_hash_bytes = web3.eth.send_raw_transaction(signed.rawTransaction)
        tx_hash = web3.to_hex(tx_hash_bytes)

    console.print(f"[bold]TX submitted:[/bold] [cyan]{tx_hash}[/cyan]")

    with console.status("[bold cyan]Waiting for confirmation…[/bold cyan]"):
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash_bytes)

    if receipt.get("status") == 1:
        console.print(
            Panel(
                f"[bold green]✓ {len(order_keys)} order(s) cancelled[/bold green]\n[bold]Block:[/bold]    [cyan]{receipt['blockNumber']}[/cyan]\n[bold]Gas used:[/bold] [cyan]{receipt['gasUsed']:,}[/cyan]\n[bold]TX:[/bold]       [cyan]{tx_hash}[/cyan]",
                title="[bold green]Success[/bold green]",
                border_style="green",
                expand=False,
            )
        )
    else:
        console.print(
            Panel(
                f"[bold red]Transaction reverted[/bold red]\n[dim]{tx_hash}[/dim]",
                title="[bold red]Failed[/bold red]",
                border_style="red",
                expand=False,
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

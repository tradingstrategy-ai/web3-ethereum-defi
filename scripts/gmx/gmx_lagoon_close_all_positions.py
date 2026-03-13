"""
GMX Lagoon Close All Positions Script

Closes all open GMX positions held by a Lagoon vault Safe, using the
TradingStrategyModuleV0 ``performCall`` pattern.  All configuration is
read from environment variables — no secrets file required.

Required environment variables
-------------------------------

- ``JSON_RPC_ARBITRUM`` — Arbitrum RPC URL (space-separated multi-provider format)
- ``GMX_PRIVATE_KEY``   — Asset-manager private key (hex, e.g. ``0x...``)
- ``LAGOON_VAULT_ADDRESS`` — Lagoon ERC-4626 vault address

The Safe address is discovered automatically from the vault via ``fetch_info()``.

Optional environment variables
--------------------------------

- ``SLIPPAGE_PERCENT``  — Slippage tolerance in percent (default ``1.0``)

Usage
-----

.. code-block:: shell

    export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
    export GMX_PRIVATE_KEY=0x...
    export LAGOON_VAULT_ADDRESS=0xE3D5595707b2b75B3F25fBCc9A212A547d6E29ca

    cd deps/web3-ethereum-defi
    poetry run python scripts/gmx/gmx_lagoon_close_all_positions.py
"""

import logging
import os
import sys
import time

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.events import extract_order_key_from_receipt
from eth_defi.gmx.gas_monitor import GasMonitorConfig
from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.gmx.order_tracking import check_order_status, is_order_pending
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.verification import verify_gmx_order_execution
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)
console = Console()


def display_positions(positions: dict) -> None:
    """Display open positions in a formatted table.

    :param positions:
        Dictionary of position key -> position data returned by
        :class:`~eth_defi.gmx.core.open_positions.GetOpenPositions`.
    """
    if not positions:
        console.print("[yellow]No open positions found.[/yellow]")
        return

    table = Table(title="Open GMX Positions")
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
    wallet: LagoonGMXTradingWallet,
    web3,
    chain: str,
    position_key: str,
    position_data: dict,
    slippage_percent: float = 0.01,
    max_wait_seconds: int = 120,
) -> bool:
    """Close a single GMX position via the Lagoon vault.

    :param trading_client:
        :class:`~eth_defi.gmx.trading.GMXTrading` instance.

    :param wallet:
        :class:`~eth_defi.gmx.lagoon.wallet.LagoonGMXTradingWallet` that wraps
        transactions through ``TradingStrategyModuleV0.performCall()``.

    :param web3:
        Web3 instance connected to Arbitrum.

    :param chain:
        Chain name (e.g. ``"arbitrum"``).

    :param position_key:
        Position key string (e.g. ``"ETH_long"``).

    :param position_data:
        Position data dictionary from :class:`~eth_defi.gmx.core.open_positions.GetOpenPositions`.

    :param slippage_percent:
        Slippage tolerance as a decimal fraction (``0.01`` = 1 %).

    :param max_wait_seconds:
        Maximum seconds to wait for keeper execution.

    :return:
        ``True`` if the position was successfully closed, ``False`` otherwise.
    """
    console.print(f"\n[blue]Closing position: {position_key}[/blue]")
    console.print(f"  Market: {position_data['market_symbol']}")
    console.print(f"  Type: {'LONG' if position_data['is_long'] else 'SHORT'}")
    console.print(f"  Size: ${position_data['position_size']:.2f}")

    try:
        market_symbol = position_data["market_symbol"]
        collateral_symbol = position_data["collateral_token"]
        is_long = position_data["is_long"]
        size_usd = position_data["position_size"]

        # Map internal token names used by the positions endpoint back to the
        # symbols expected by the trading API.
        # GMX v2 positions return "WETH" for ETH markets; the trading API expects "ETH".
        # BTC markets use "BTC" in both the positions endpoint and the trading API — no mapping needed.
        reverse_symbol_mapping = {
            "WETH": "ETH",
        }

        if market_symbol in reverse_symbol_mapping:
            mapped = reverse_symbol_mapping[market_symbol]
            console.print(f"  Mapping market symbol '{market_symbol}' -> '{mapped}'")
            market_symbol = mapped

        leverage = position_data.get("leverage", 1.0)
        if leverage > 100 or leverage < 0.1:
            console.print(f"  [yellow]Warning: abnormal leverage {leverage:.2f}x, using size/10[/yellow]")
            initial_collateral_delta = size_usd / 10
        else:
            initial_collateral_delta = size_usd / leverage

        if initial_collateral_delta < 0.1:
            initial_collateral_delta = size_usd

        start_token_symbol = collateral_symbol

        console.print(f"  Slippage: {slippage_percent * 100:.1f}%")

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

        # Sign through LagoonGMXTradingWallet — wraps in performCall()
        transaction = order_result.transaction.copy()
        if "nonce" in transaction:
            del transaction["nonce"]

        signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

        console.print(f"  Order creation tx: [yellow]{tx_hash.hex()}[/yellow]")
        console.print("  Waiting for order creation confirmation...")

        creation_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        if creation_receipt["status"] != 1:
            console.print("  [red]Order creation transaction reverted![/red]")
            return False

        console.print(f"  Order created in block {creation_receipt['blockNumber']}")

        try:
            order_key = extract_order_key_from_receipt(web3, creation_receipt)
            console.print(f"  Order key: [cyan]{order_key.hex()[:16]}...[/cyan]")
        except ValueError as e:
            console.print(f"  [red]Failed to extract order key: {e}[/red]")
            return False

        # Poll until the keeper has executed (or we time out)
        console.print("  Waiting for keeper execution...")
        start_time = time.time()
        poll_interval = 2

        while time.time() - start_time < max_wait_seconds:
            if not is_order_pending(web3, order_key, chain):
                break
            elapsed = int(time.time() - start_time)
            console.print(f"  Still pending... ({elapsed}s)")
            time.sleep(poll_interval)
        else:
            console.print(f"  [yellow]Timed out waiting for keeper ({max_wait_seconds}s)[/yellow]")
            console.print("  Order may still be executed later by keepers.")
            return False

        status_result = check_order_status(web3, order_key, chain)

        if not status_result.execution_receipt:
            console.print("  [yellow]Order executed but receipt not found[/yellow]")
            return False

        console.print(f"  Keeper tx: [yellow]{status_result.execution_tx_hash[:16]}...[/yellow]")

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


def main() -> None:
    """Main entry point."""
    FORMAT = "%(message)s"
    logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
    logging.getLogger("eth_defi").setLevel(logging.INFO)
    logging.getLogger("eth_defi.gmx.trading").setLevel(logging.INFO)
    logging.getLogger("eth_defi.gmx.gas_monitor").setLevel(logging.INFO)

    # --- Read configuration from environment ---
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        console.print("[red]JSON_RPC_ARBITRUM environment variable is not set.[/red]")
        sys.exit(1)

    private_key = os.environ.get("GMX_PRIVATE_KEY")
    if not private_key:
        console.print("[red]GMX_PRIVATE_KEY environment variable is not set.[/red]")
        sys.exit(1)

    vault_address_raw = os.environ.get("LAGOON_VAULT_ADDRESS")
    if not vault_address_raw:
        console.print("[red]LAGOON_VAULT_ADDRESS environment variable is not set.[/red]")
        sys.exit(1)

    slippage_percent = float(os.environ.get("SLIPPAGE_PERCENT", "1.0")) / 100.0

    console.print("\n[bold]GMX Lagoon — Close All Positions[/bold]\n")
    console.print(f"Vault address  : {vault_address_raw}")
    console.print(f"Slippage       : {slippage_percent * 100:.1f}%")

    # --- Connect to Arbitrum ---
    console.print("\nConnecting to Arbitrum...")
    # RPC URL may be space-separated (multi-provider format); pass as-is to create_multi_provider_web3
    web3 = create_multi_provider_web3(rpc_url)

    try:
        block_number = web3.eth.block_number
        chain_id = web3.eth.chain_id
        chain = get_chain_name(chain_id).lower()
        console.print(f"Connected — chain: {chain}, chain_id: {chain_id}, block: {block_number}")
    except Exception as e:
        console.print(f"[red]Failed to connect: {e}[/red]")
        sys.exit(1)

    # --- Initialise asset manager hot wallet ---
    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)

    eth_balance = web3.eth.get_balance(hot_wallet.address)
    eth_balance_eth = float(web3.from_wei(eth_balance, "ether"))
    console.print(f"Asset manager  : {hot_wallet.address}")
    console.print(f"ETH balance    : {eth_balance_eth:.6f}")

    if eth_balance_eth < 0.001:
        console.print("[red]Warning: low ETH balance for gas fees![/red]")

    # --- Initialise Lagoon vault ---
    console.print("\nInitialising Lagoon vault...")
    vault_spec = VaultSpec(chain_id, vault_address_raw)
    vault = LagoonVault(web3, vault_spec)

    try:
        vault_info = vault.fetch_info()
    except Exception as e:
        console.print(f"[red]vault.fetch_info() failed: {e}[/red]")
        sys.exit(1)

    modules = vault_info.get("modules", [])
    if not modules:
        console.print("[red]No TradingStrategyModuleV0 found on the Safe. Deploy and enable the module first.[/red]")
        sys.exit(1)

    vault.trading_strategy_module_address = modules[0]
    safe_address = vault.safe_address

    console.print(f"Safe address   : {safe_address}")
    console.print(f"Module address : {modules[0]}")

    # --- Build Lagoon wallet ---
    lagoon_wallet = LagoonGMXTradingWallet(
        vault=vault,
        asset_manager=hot_wallet,
        gas_buffer=500_000,
        forward_eth=True,
    )

    # --- Fetch open positions (owned by Safe) ---
    console.print("\nFetching open positions...")
    config = GMXConfig(web3, user_wallet_address=safe_address, wallet=lagoon_wallet)
    gas_config = GasMonitorConfig(enabled=True)
    trading_client = GMXTrading(config, gas_monitor_config=gas_config)
    positions_fetcher = GetOpenPositions(config)
    positions = positions_fetcher.get_data(safe_address)

    display_positions(positions)

    if not positions:
        console.print("\n[green]No positions to close. Done![/green]")
        sys.exit(0)

    console.print(f"\n[yellow]Found {len(positions)} position(s) to close.[/yellow]")
    console.print("[yellow]Press Enter to continue or Ctrl+C to abort...[/yellow]")

    try:
        input()
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted by user.[/yellow]")
        sys.exit(0)

    # --- Close each position ---
    console.print("\n[bold]Closing positions...[/bold]")

    closed = 0
    failed = 0

    for position_key, position_data in positions.items():
        success = close_position(
            trading_client=trading_client,
            wallet=lagoon_wallet,
            web3=web3,
            chain=chain,
            position_key=position_key,
            position_data=position_data,
            slippage_percent=slippage_percent,
            max_wait_seconds=120,
        )

        if success:
            closed += 1
        else:
            failed += 1

    # --- Summary ---
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

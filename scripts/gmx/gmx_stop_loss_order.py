"""
GMX Stop Loss Order Script for Arbitrum Sepolia

Opens a position and creates a stop loss order on GMX Arbitrum Sepolia testnet.

This script demonstrates:
1. Opening a leveraged position
2. Creating a standalone stop loss order for that position

Usage
-----

With environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_stop_loss_order.py

Environment Variables
---------------------

- ``PRIVATE_KEY``: Your wallet's private key (required)
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint

See Also
--------

- :mod:`eth_defi.gmx.trading` - GMX trading module
- :mod:`eth_defi.gmx.order.sltp_order` - SL/TP order module
- :py:mod:`tests.gmx.debug_sltp` - Fork testing script
"""

import os
import sys
import time

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_token_address_normalized
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from rich.console import Console

console = Console()

# Configuration
EXECUTION_BUFFER = 30  # Higher buffer for testnet reliability
SIZE_USD = 10  # Position size in USD
LEVERAGE = 1.5  # Leverage multiplier
STOP_LOSS_PERCENT = 0.05  # 5% stop loss
MARKET_SYMBOL = "ETH"
COLLATERAL_SYMBOL = "USDC"
IS_LONG = True  # Long position


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
    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    private_key = os.environ.get("PRIVATE_KEY")

    if not rpc_url:
        console.print("[red]Error: ARBITRUM_SEPOLIA_RPC_URL environment variable not set[/red]")
        sys.exit(1)

    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    console.print("\n[bold green]=== GMX Stop Loss Order - Arbitrum Sepolia ===[/bold green]\n")

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

    # Create GMX config and trading client
    config = GMXConfig(web3, user_wallet_address=wallet_address)
    trading = GMXTrading(config)

    # Check for existing positions
    console.print("\n[bold]Checking existing positions...[/bold]")
    position_reader = GetOpenPositions(config)
    positions = position_reader.get_data(wallet_address)

    if positions:
        console.print(f"[green]Found {len(positions)} existing position(s)[/green]")
        pos_key, pos_data = list(positions.items())[0]
        entry_price = pos_data["entry_price"]
        position_size = pos_data["position_size"]
        is_long = pos_data.get("is_long", IS_LONG)
        market_symbol = pos_data.get("market_symbol", MARKET_SYMBOL)
        collateral_symbol = pos_data.get("collateral_symbol", COLLATERAL_SYMBOL)

        console.print(f"  Using existing position:")
        console.print(f"    Market: {market_symbol}")
        console.print(f"    Side: {'Long' if is_long else 'Short'}")
        console.print(f"    Size: ${position_size:,.2f}")
        console.print(f"    Entry Price: ${entry_price:,.2f}")
    else:
        # No existing position - open one first
        console.print("[yellow]No existing positions found. Opening a new position...[/yellow]")

        # Approve USDC for GMX SyntheticsRouter
        console.print("\n[bold]Step 1: Approving USDC...[/bold]")
        contract_addresses = get_contract_addresses(chain)
        spender_address = contract_addresses.syntheticsrouter

        current_allowance = usdc_token.contract.functions.allowance(wallet_address, spender_address).call()
        required_amount = 1_000_000 * (10**6)  # 1M USDC approval

        if current_allowance < required_amount:
            approve_tx = usdc_token.contract.functions.approve(spender_address, required_amount).build_transaction(
                {
                    "from": wallet_address,
                    "gas": 100000,
                    "gasPrice": web3.eth.gas_price,
                }
            )
            if "nonce" in approve_tx:
                del approve_tx["nonce"]

            signed_approve = wallet.sign_transaction_with_new_nonce(approve_tx)
            approve_hash = web3.eth.send_raw_transaction(signed_approve.rawTransaction)
            console.print(f"  Approval TX: {approve_hash.hex()}")

            approve_receipt = web3.eth.wait_for_transaction_receipt(approve_hash)
            if approve_receipt["status"] == 1:
                console.print(f"  [green]USDC approved[/green]")
            else:
                console.print(f"  [red]Approval failed[/red]")
                sys.exit(1)
        else:
            console.print(f"  [green]USDC already approved[/green]")

        # Open position
        console.print(f"\n[bold]Step 2: Opening position...[/bold]")
        console.print(f"  Market: {MARKET_SYMBOL}")
        console.print(f"  Collateral: {COLLATERAL_SYMBOL}")
        console.print(f"  Side: {'Long' if IS_LONG else 'Short'}")
        console.print(f"  Size: ${SIZE_USD}")
        console.print(f"  Leverage: {LEVERAGE}x")

        order = trading.open_position(
            market_symbol=MARKET_SYMBOL,
            collateral_symbol=COLLATERAL_SYMBOL,
            start_token_symbol=COLLATERAL_SYMBOL,
            is_long=IS_LONG,
            size_delta_usd=SIZE_USD,
            leverage=LEVERAGE,
            slippage_percent=0.005,
            execution_buffer=EXECUTION_BUFFER,
        )

        console.print(f"  [green]Order created[/green]")
        console.print(f"    Mark Price: ${order.mark_price:,.2f}")
        console.print(f"    Execution Fee: {order.execution_fee / 10**18:.6f} ETH")

        # Submit order
        transaction = order.transaction
        if "nonce" in transaction:
            del transaction["nonce"]

        signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        console.print(f"  TX Hash: {tx_hash.hex()}")

        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt["status"] != 1:
            console.print("[red]Open position transaction failed[/red]")
            assert_transaction_success_with_explanation(web3, tx_hash)
            sys.exit(1)

        order_keys = verify_orders_created(receipt)
        if order_keys:
            console.print(f"  Order Key: {order_keys[0].hex()}")

        console.print(f"  [green]Position order submitted![/green]")
        console.print(f"  [dim]Note: On testnet, keepers will execute the order. This may take a moment.[/dim]")

        # Wait for keeper to execute and position to appear
        console.print("\n[bold]Waiting for position to be created by keeper...[/bold]")
        for i in range(30):  # Wait up to 30 seconds
            time.sleep(1)
            positions = position_reader.get_data(wallet_address)
            if positions:
                break
            console.print(f"  Waiting... ({i + 1}s)")

        if not positions:
            console.print("[yellow]Position not yet visible. On testnet, keepers may take longer.[/yellow]")
            console.print("[yellow]You can run this script again once the position is created.[/yellow]")
            sys.exit(0)

        pos_key, pos_data = list(positions.items())[0]
        entry_price = pos_data["entry_price"]
        position_size = pos_data["position_size"]
        is_long = IS_LONG
        market_symbol = MARKET_SYMBOL
        collateral_symbol = COLLATERAL_SYMBOL

        console.print(f"\n[green]Position created![/green]")
        console.print(f"  Entry Price: ${entry_price:,.2f}")
        console.print(f"  Size: ${position_size:,.2f}")

    # Create Stop Loss
    console.print(f"\n[bold cyan]Creating Stop Loss ({STOP_LOSS_PERCENT * 100:.1f}%)...[/bold cyan]")

    # Calculate trigger price for display
    if is_long:
        sl_trigger = entry_price * (1 - STOP_LOSS_PERCENT)
    else:
        sl_trigger = entry_price * (1 + STOP_LOSS_PERCENT)

    console.print(f"  Trigger Price: ${sl_trigger:,.2f}")

    sl_result = trading.create_stop_loss(
        market_symbol=market_symbol,
        collateral_symbol=collateral_symbol,
        is_long=is_long,
        position_size_usd=position_size,
        entry_price=entry_price,
        stop_loss_percent=STOP_LOSS_PERCENT,
        execution_buffer=EXECUTION_BUFFER,
    )

    console.print(f"  Execution Fee: {sl_result.execution_fee / 10**18:.6f} ETH")

    # Submit Stop Loss
    sl_tx = sl_result.transaction
    if "nonce" in sl_tx:
        del sl_tx["nonce"]

    signed_sl = wallet.sign_transaction_with_new_nonce(sl_tx)
    sl_hash = web3.eth.send_raw_transaction(signed_sl.rawTransaction)
    console.print(f"  TX Hash: {sl_hash.hex()}")

    sl_receipt = web3.eth.wait_for_transaction_receipt(sl_hash)

    if sl_receipt["status"] == 1:
        sl_order_keys = verify_orders_created(sl_receipt)
        console.print(f"[green]Stop Loss order created: {sl_order_keys[0].hex() if sl_order_keys else 'N/A'}[/green]")
    else:
        console.print("[red]Stop Loss order failed[/red]")
        assert_transaction_success_with_explanation(web3, sl_hash)
        sys.exit(1)

    # Summary
    console.print("\n" + "=" * 60)
    console.print("[bold green]Stop Loss Order Submitted Successfully![/bold green]")
    console.print("=" * 60)
    console.print(f"\n  Position: {market_symbol} {'Long' if is_long else 'Short'}")
    console.print(f"  Size: ${position_size:,.2f}")
    console.print(f"  Entry Price: ${entry_price:,.2f}")
    console.print(f"  Stop Loss Trigger: ${sl_trigger:,.2f}")
    console.print("\n[dim]Note: SL order is pending until price triggers it.[/dim]")
    console.print("[dim]On testnet, keepers will execute when conditions are met.[/dim]")


if __name__ == "__main__":
    main()

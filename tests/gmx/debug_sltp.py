"""GMX Stop Loss / Take Profit Order Debug Script - Fork Testing

Creates and verifies SL/TP orders on forked networks for testing.

MODES:
1. Anvil fork (default):   python tests/gmx/debug_sltp.py --fork
2. Tenderly fork:          python tests/gmx/debug_sltp.py --td
3. Custom Anvil RPC:       python tests/gmx/debug_sltp.py --anvil-rpc http://localhost:8545

Required environment variables:
- PRIVATE_KEY: Private key for signing transactions
- ARBITRUM_CHAIN_JSON_RPC: RPC endpoint for Anvil fork
- TD_ARB: Tenderly fork URL (for --td mode)
"""

import argparse
import logging
import os
import sys
import time

from eth_utils import to_checksum_address
from rich.console import Console
from rich.logging import RichHandler
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_token_address_normalized
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import (
    execute_order_as_keeper,
    extract_order_key_from_receipt,
    setup_mock_oracle,
)

FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
logger = logging.getLogger("rich")

console = Console()

# Fork test configuration
LARGE_USDC_HOLDER = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
LARGE_WETH_HOLDER = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")
EXECUTION_BUFFER = 30


def setup_fork_network(web3: Web3):
    """Setup mock oracle and display network info."""
    block_number = web3.eth.block_number
    chain_id = web3.eth.chain_id
    chain = get_chain_name(chain_id).lower()

    console.print(f"  Block: {block_number}")
    console.print(f"  Chain ID: {chain_id}")
    console.print(f"  Chain: {chain}")

    console.print("\n[dim]Setting up mock oracle (fetching on-chain prices)...[/dim]")
    setup_mock_oracle(web3)
    console.print(f"[dim]Mock oracle configured with on-chain prices[/dim]\n")

    return chain


def fund_wallet_anvil(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Anvil fork using anvil_setBalance and whale transfers."""
    console.print("\n[bold]Funding wallet (Anvil mode)...[/bold]")

    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]ETH balance: 100 ETH[/green]")

    gas_eth = 1 * 10**18
    web3.provider.make_request("anvil_setBalance", [LARGE_USDC_HOLDER, hex(gas_eth)])
    web3.provider.make_request("anvil_setBalance", [LARGE_WETH_HOLDER, hex(gas_eth)])

    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        usdc_token = fetch_erc20_details(web3, usdc_address)
        usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": LARGE_USDC_HOLDER})
        balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]USDC balance: {balance / 10**6:.2f} USDC[/green]")

    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        weth_token = fetch_erc20_details(web3, weth_address)
        weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": LARGE_WETH_HOLDER})
        balance = weth_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]WETH balance: {balance / 10**18:.2f} WETH[/green]")


def fund_wallet_tenderly(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Tenderly fork using Tenderly RPC methods."""
    console.print("\n[bold]Funding wallet (Tenderly mode)...[/bold]")

    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]ETH balance: 100 ETH[/green]")

    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        web3.provider.make_request("tenderly_setErc20Balance", [usdc_address, wallet_address, hex(usdc_amount)])
        console.print(f"  [green]USDC balance: 100,000 USDC[/green]")

    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        web3.provider.make_request("tenderly_setErc20Balance", [weth_address, wallet_address, hex(weth_amount)])
        console.print(f"  [green]WETH balance: 1,000 WETH[/green]")


def verify_orders_created(receipt: dict, expected_count: int = 1) -> list[bytes]:
    """Extract and verify order keys from transaction receipt."""
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


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX SL/TP Order Debug Script - Fork Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    fork_group.add_argument("--td", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    fork_group.add_argument("--anvil-rpc", type=str, help="Connect to existing Anvil RPC")

    parser.add_argument("--size", type=float, default=100.0, help="Position size in USD (default: 100)")
    parser.add_argument("--sl-percent", type=float, default=0.05, help="Stop loss percentage (default: 0.05 = 5%%)")
    parser.add_argument("--tp-percent", type=float, default=0.10, help="Take profit percentage (default: 0.10 = 10%%)")
    parser.add_argument("--bundled", action="store_true", help="Use bundled order (open + SL + TP in one tx)", default=True)
    parser.add_argument("--standalone", action="store_true", help="Use standalone orders (open first, then SL/TP)")

    return parser.parse_args()


def main():
    """Main execution flow."""
    args = parse_arguments()

    # Default to standalone if neither specified (more reliable)
    if not args.bundled and not args.standalone:
        args.standalone = True

    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    launch = None
    is_tenderly = False

    try:
        console.print("\n[bold green]=== GMX SL/TP Fork Test ===[/bold green]\n")

        # ========================================================================
        # STEP 1: Connect to Network
        # ========================================================================

        if args.td:
            tenderly_rpc = os.environ.get("TD_ARB")
            if not tenderly_rpc:
                console.print("[red]Error: TD_ARB environment variable not set[/red]")
                sys.exit(1)

            console.print("Using Tenderly fork...")
            web3 = create_multi_provider_web3(tenderly_rpc)
            is_tenderly = True

        elif args.anvil_rpc:
            console.print(f"Using custom Anvil at {args.anvil_rpc}...")
            web3 = create_multi_provider_web3(args.anvil_rpc, default_http_timeout=(3.0, 180.0))

        else:
            fork_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
            if not fork_rpc:
                console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
                sys.exit(1)

            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[LARGE_USDC_HOLDER, LARGE_WETH_HOLDER],
            )

            web3 = create_multi_provider_web3(
                launch.json_rpc_url,
                default_http_timeout=(3.0, 180.0),
            )
            console.print(f"  Anvil fork started on {launch.json_rpc_url}")

        chain = setup_fork_network(web3)

        # ========================================================================
        # STEP 2: Setup Wallet
        # ========================================================================
        console.print("\n[bold]Setting up wallet...[/bold]")
        wallet = HotWallet.from_private_key(private_key)
        wallet.sync_nonce(web3)
        wallet_address = wallet.get_main_address()
        console.print(f"  Wallet: {wallet_address}")

        tokens = {
            "WETH": get_token_address_normalized(chain, "WETH"),
            "USDC": get_token_address_normalized(chain, "USDC"),
        }
        for symbol, address in tokens.items():
            console.print(f"  {symbol}: {address}")

        # ========================================================================
        # STEP 3: Fund Wallet
        # ========================================================================
        if is_tenderly:
            fund_wallet_tenderly(web3, wallet_address, tokens)
        else:
            fund_wallet_anvil(web3, wallet_address, tokens)

        # ========================================================================
        # STEP 4: Setup Trading Client
        # ========================================================================
        console.print("\n[bold]Setting up trading client...[/bold]")
        config = GMXConfig(web3, user_wallet_address=wallet_address)
        trading = GMXTrading(config)

        size_usd = args.size
        leverage = 2.5

        if args.bundled:
            # ================================================================
            # BUNDLED: Open + SL + TP in single transaction
            # ================================================================
            console.print("\n[bold cyan]Creating BUNDLED order (Open + SL + TP)...[/bold cyan]")
            console.print(f"  Position Size: ${size_usd}")
            console.print(f"  Leverage: {leverage}x")
            console.print(f"  Stop Loss: {args.sl_percent * 100:.1f}%")
            console.print(f"  Take Profit: {args.tp_percent * 100:.1f}%")

            result = trading.open_position_with_sltp(
                market_symbol="ETH",
                collateral_symbol="ETH",
                start_token_symbol="ETH",
                is_long=False,
                size_delta_usd=size_usd,
                leverage=leverage,
                stop_loss_percent=args.sl_percent,
                take_profit_percent=args.tp_percent,
                slippage_percent=0.005,
                execution_buffer=EXECUTION_BUFFER,
            )

            console.print(f"\n[green]Bundled order created[/green]")
            console.print(f"  Entry Price: ${result.entry_price:,.2f}")
            console.print(f"  Stop Loss Trigger: ${result.stop_loss_trigger_price:,.2f}")
            console.print(f"  Take Profit Trigger: ${result.take_profit_trigger_price:,.2f}")
            console.print(f"  Total Execution Fee: {result.total_execution_fee / 10**18:.6f} ETH")

            # Submit bundled transaction
            console.print("\n[bold]Submitting bundled transaction...[/bold]")
            transaction = result.transaction
            if "nonce" in transaction:
                del transaction["nonce"]

            signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            console.print(f"  TX Hash: {tx_hash.hex()}")

            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt["status"] == 1:
                console.print(f"[green]Transaction succeeded[/green]")

                # Verify orders created
                order_keys = verify_orders_created(receipt)
                console.print(f"\n[green]Orders created: {len(order_keys)}[/green]")
                for i, key in enumerate(order_keys):
                    order_type = ["Main (Increase)", "Stop Loss", "Take Profit"][i] if i < 3 else f"Order {i + 1}"
                    console.print(f"  {order_type}: {key.hex()}")

                # Execute main order as keeper
                if order_keys:
                    console.print("\n[bold]Executing main order as keeper...[/bold]")
                    main_order_key = order_keys[0]
                    exec_receipt, keeper_address = execute_order_as_keeper(web3, main_order_key)
                    console.print(f"[green]Main order executed[/green]")

                    # Verify position
                    time.sleep(1)
                    position_reader = GetOpenPositions(config)
                    positions = position_reader.get_data(wallet_address)
                    if positions:
                        console.print(f"[green]Position verified ({len(positions)} position(s))[/green]")
                        console.print(f"{positions=}")
            else:
                console.print(f"[red]Transaction failed[/red]")
                assert_transaction_success_with_explanation(web3, tx_hash)

        else:
            # ================================================================
            # STANDALONE: Open position first, then add SL/TP
            # ================================================================
            console.print("\n[bold cyan]Creating STANDALONE orders...[/bold cyan]")

            # Step 1: Open position using simple interface
            console.print("\n[bold]Step 1: Opening position...[/bold]")
            console.print(f"  Size: ${size_usd} at {leverage}x leverage")

            order = trading.open_position(
                market_symbol="ETH",
                collateral_symbol="ETH",
                start_token_symbol="ETH",
                is_long=False,
                size_delta_usd=size_usd,
                leverage=leverage,
                slippage_percent=0.005,
                execution_buffer=EXECUTION_BUFFER,
            )

            console.print(f"[green]Order created[/green]")
            console.print(f"  Mark Price: ${order.mark_price:,.2f}")
            console.print(f"  Execution Fee: {order.execution_fee / 10**18:.6f} ETH")

            # Submit order
            transaction = order.transaction
            if "nonce" in transaction:
                del transaction["nonce"]

            signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            console.print(f"  TX Hash: {tx_hash.hex()}")

            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt["status"] != 1:
                console.print("[red]Open position failed[/red]")
                assert_transaction_success_with_explanation(web3, tx_hash)
                sys.exit(1)

            # Execute order as keeper
            order_key = extract_order_key_from_receipt(receipt)
            console.print(f"  Order Key: {order_key.hex()}")

            console.print("\n[bold]Executing order as keeper...[/bold]")
            exec_receipt, keeper = execute_order_as_keeper(web3, order_key)
            console.print(f"[green]Position opened[/green]")

            # Get position details
            time.sleep(1)
            position_reader = GetOpenPositions(config)
            positions = position_reader.get_data(wallet_address)

            if not positions:
                console.print("[red]No position found after opening[/red]")
                sys.exit(1)

            pos_key, pos_data = list(positions.items())[0]
            entry_price = pos_data["entry_price"]
            position_size = pos_data["position_size"]

            console.print(f"\n[green]Position verified[/green]")
            console.print(f"  Entry Price: ${entry_price:,.2f}")
            console.print(f"  Size: ${position_size:.2f}")

            # Step 2: Create Stop Loss - simple interface!
            console.print(f"\n[bold]Step 2: Creating Stop Loss ({args.sl_percent * 100:.1f}%)...[/bold]")

            sl_result = trading.create_stop_loss(
                market_symbol="ETH",
                collateral_symbol="ETH",
                is_long=False,
                position_size_usd=position_size,
                entry_price=entry_price,
                stop_loss_percent=args.sl_percent,
                execution_buffer=EXECUTION_BUFFER,
            )

            sl_trigger = entry_price * (1 - args.sl_percent)
            console.print(f"  Trigger Price: ${sl_trigger:,.2f}")
            console.print(f"  Execution Fee: {sl_result.execution_fee / 10**18:.6f} ETH")

            # Submit SL
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

            # Step 3: Create Take Profit - simple interface!
            console.print(f"\n[bold]Step 3: Creating Take Profit ({args.tp_percent * 100:.1f}%)...[/bold]")

            tp_result = trading.create_take_profit(
                market_symbol="ETH",
                collateral_symbol="ETH",
                is_long=False,
                position_size_usd=position_size,
                entry_price=entry_price,
                take_profit_percent=args.tp_percent,
                execution_buffer=EXECUTION_BUFFER,
            )

            tp_trigger = entry_price * (1 + args.tp_percent)
            console.print(f"  Trigger Price: ${tp_trigger:,.2f}")
            console.print(f"  Execution Fee: {tp_result.execution_fee / 10**18:.6f} ETH")

            # Submit TP
            tp_tx = tp_result.transaction
            if "nonce" in tp_tx:
                del tp_tx["nonce"]

            signed_tp = wallet.sign_transaction_with_new_nonce(tp_tx)
            tp_hash = web3.eth.send_raw_transaction(signed_tp.rawTransaction)
            console.print(f"  TX Hash: {tp_hash.hex()}")

            tp_receipt = web3.eth.wait_for_transaction_receipt(tp_hash)

            if tp_receipt["status"] == 1:
                tp_order_keys = verify_orders_created(tp_receipt)
                console.print(f"[green]Take Profit order created: {tp_order_keys[0].hex() if tp_order_keys else 'N/A'}[/green]")
            else:
                console.print("[red]Take Profit order failed[/red]")
                assert_transaction_success_with_explanation(web3, tp_hash)

        # ========================================================================
        # FINAL: Summary
        # ========================================================================
        console.print("\n" + "=" * 60)
        console.print("[bold green]SL/TP Debug Test Completed![/bold green]")
        console.print("=" * 60)
        console.print("\n[dim]Note: SL/TP orders are pending until price triggers them.[/dim]")
        console.print("[dim]On mainnet, keepers will execute when conditions are met.[/dim]")

    except Exception as e:
        console.print(f"\n[red]Error: {str(e)}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    finally:
        if launch:
            console.print("\n[dim]Shutting down Anvil...[/dim]")
            launch.close()


if __name__ == "__main__":
    main()

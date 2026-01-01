"""GMX CCXT Stop Loss and Take Profit Order Creation Test - Fork Testing

Creates and executes GMX CCXT orders with stop-loss and take-profit on forked networks.

MODES:
1. Anvil fork (default):   python tests/gmx/debug_ccxt_sltp.py --fork
2. Tenderly fork:          python tests/gmx/debug_ccxt_sltp.py --td
3. Custom Anvil RPC:       python tests/gmx/debug_ccxt_sltp.py --anvil-rpc http://localhost:8545

MARKET SELECTION:
- Default: ETH/USDC with ETH collateral
- Use --btc for BTC/USDC with USDC collateral

MARKET LOADING:
- Default: RPC/Core Markets (slower but correct)
- Use --graphql for GraphQL loading (faster but may have bugs)

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
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_token_address_normalized
from eth_defi.gmx.core.open_positions import GetOpenPositions
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

# Configure logging
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
logger = logging.getLogger("rich")

console = Console()

# Fork test configuration
FORK_BLOCK = 392496384
LARGE_USDC_HOLDER = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
LARGE_WETH_HOLDER = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")
GMX_KEEPER_ADDRESS = to_checksum_address("0x7452c558d45f8afc8c83dae62c3f8a5be19c71f6")
EXECUTION_BUFFER = 30


def setup_fork_network(web3: Web3):
    """Setup mock oracle and display network info.

    Follows GMX forked-env-example pattern:
    - Fetches actual on-chain oracle prices before mocking
    - This ensures prices pass GMX's validation
    """
    block_number = web3.eth.block_number
    chain_id = web3.eth.chain_id
    chain = get_chain_name(chain_id).lower()

    console.print(f"  Block: {block_number}")
    console.print(f"  Chain ID: {chain_id}")
    console.print(f"  Chain: {chain}")

    # Setup mock oracle - prices fetched dynamically from chain
    console.print("\n[dim]Setting up mock oracle (fetching on-chain prices)...[/dim]")
    setup_mock_oracle(web3)  # No hardcoded prices - fetches from chain automatically
    console.print(f"[dim]Mock oracle configured with on-chain prices[/dim]\n")

    return chain


def fund_wallet_anvil(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Anvil fork using anvil_setBalance and whale transfers."""
    console.print("\n[bold]Funding wallet (Anvil mode)...[/bold]")

    # Set ETH balance for wallet
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]ETH balance: 100 ETH[/green]")

    # Give whales some ETH for gas
    gas_eth = 100 * 10**18
    web3.provider.make_request(
        "anvil_setBalance",
        [LARGE_USDC_HOLDER, hex(gas_eth)],
    )
    web3.provider.make_request(
        "anvil_setBalance",
        [LARGE_WETH_HOLDER, hex(gas_eth)],
    )

    # Fund GMX router address for gas (needed for order execution)
    web3.provider.make_request(
        "anvil_setBalance",
        [GMX_KEEPER_ADDRESS, hex(gas_eth)],
    )
    console.print(f"  [green]GMX Keeper funded: 100 ETH[/green]")

    # Transfer USDC from whale
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        usdc_token = fetch_erc20_details(web3, usdc_address)
        usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": LARGE_USDC_HOLDER})
        balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]USDC balance: {balance / 10**6:.2f} USDC[/green]")

    # Transfer WETH from whale (needed for ETH/USDC market with ETH collateral)
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        weth_token = fetch_erc20_details(web3, weth_address)
        weth_token.contract.functions.transfer(wallet_address, weth_amount).transact(
            {"from": LARGE_WETH_HOLDER},
        )
        balance = weth_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]WETH balance: {balance / 10**18:.2f} WETH[/green]")


def fund_wallet_tenderly(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Tenderly fork using Tenderly RPC methods."""
    console.print("\n[bold]Funding wallet (Tenderly mode)...[/bold]")

    # Set ETH balance
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request(
        "tenderly_setBalance",
        [wallet_address, hex(eth_amount_wei)],
    )
    console.print(f"  [green]ETH balance: 100 ETH[/green]")

    # Fund GMX router address for gas (needed for order execution)
    gas_eth = 1 * 10**18
    web3.provider.make_request(
        "tenderly_setBalance",
        [GMX_KEEPER_ADDRESS, hex(gas_eth)],
    )
    console.print(f"  [green]GMX Keeper funded: 1 ETH[/green]")

    # Set USDC balance (needed for both ETH and BTC markets)
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        web3.provider.make_request(
            "tenderly_setErc20Balance",
            [usdc_address, wallet_address, hex(usdc_amount)],
        )
        console.print(f"  [green]USDC balance: 100,000 USDC[/green]")

    # Set WETH balance (needed for ETH/USDC market with ETH collateral)
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        web3.provider.make_request(
            "tenderly_setErc20Balance",
            [weth_address, wallet_address, hex(weth_amount)],
        )
        console.print(f"  [green]WETH balance: 1,000 WETH[/green]")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX CCXT SL/TP Order Creation Test - Fork Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Fork mode options (mutually exclusive)
    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    fork_group.add_argument("--td", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    fork_group.add_argument("--anvil-rpc", type=str, help="Connect to existing Anvil RPC (e.g., http://127.0.0.1:8545)")

    # Position size override
    parser.add_argument("--size", type=float, default=10.0, help="Position size in USD (default: 10)")

    # SL/TP options
    parser.add_argument("--stop-loss", type=float, default=0.05, help="Stop loss percentage (default: 0.05 = 5%%)")
    parser.add_argument("--take-profit", type=float, default=0.10, help="Take profit percentage (default: 0.10 = 10%%)")

    # Order mode
    parser.add_argument(
        "--mode",
        choices=["both", "sl-only", "tp-only"],
        default="both",
        help="SL/TP mode: both, sl-only, or tp-only (default: both)",
    )

    # Market selection
    parser.add_argument(
        "--btc",
        action="store_true",
        help="Use BTC/USDC market instead of ETH/USDC (default: ETH/USDC)",
    )

    # Market loading method
    parser.add_argument(
        "--graphql",
        action="store_true",
        help="Use GraphQL for market loading (default: RPC/Core Markets)",
    )

    return parser.parse_args()


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
    """Main execution flow."""
    args = parse_arguments()

    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    launch = None
    is_tenderly = False

    # Get wallet address from private key early (needed for Anvil unlocking)
    temp_wallet = HotWallet.from_private_key(private_key)
    wallet_address = temp_wallet.get_main_address()

    try:
        console.print("\n[bold green]=== GMX CCXT SL/TP Fork Test ===[/bold green]\n")

        # ========================================================================
        # STEP 1: Connect to Network
        # ========================================================================

        if args.td:
            # Tenderly fork mode
            tenderly_rpc = os.environ.get("TD_ARB")
            if not tenderly_rpc:
                console.print("[red]Error: TD_ARB environment variable not set[/red]")
                sys.exit(1)

            console.print("Using Tenderly fork...")
            web3 = create_multi_provider_web3(tenderly_rpc)
            is_tenderly = True

        elif args.anvil_rpc:
            # Custom Anvil RPC mode
            console.print(f"Using custom Anvil at {args.anvil_rpc}...")
            web3 = create_multi_provider_web3(args.anvil_rpc, default_http_timeout=(3.0, 180.0))

        else:
            # Anvil fork mode (default)
            fork_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
            if not fork_rpc:
                console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
                sys.exit(1)

            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[
                    wallet_address,  # User's wallet
                    LARGE_USDC_HOLDER,
                    LARGE_WETH_HOLDER,
                    GMX_KEEPER_ADDRESS,
                ],
            )

            web3 = create_multi_provider_web3(
                launch.json_rpc_url,
                default_http_timeout=(3.0, 180.0),
            )
            console.print(f"  Anvil fork started on {launch.json_rpc_url}")

        # Setup network and oracle
        chain = setup_fork_network(web3)

        # ========================================================================
        # STEP 2: Setup Wallet
        # ========================================================================
        console.print("\n[bold]Setting up wallet...[/bold]")
        wallet = temp_wallet  # Use wallet created earlier for unlocking
        wallet.sync_nonce(web3)
        console.print(f"  Wallet: {wallet_address}")

        # Get token addresses
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
        # STEP 4: Setup GMX CCXT
        # ========================================================================
        console.print("\n[bold]Setting up GMX CCXT...[/bold]")

        # Initialize CCXT GMX wrapper
        gmx_params = {
            "rpcUrl": web3.provider.endpoint_uri
            if hasattr(
                web3.provider,
                "endpoint_uri",
            )
            else None,
            "wallet": wallet,
        }

        # Add graphql_only option if flag is set
        if args.graphql:
            gmx_params["options"] = {"graphql_only": True}
            console.print("  [yellow]Using GraphQL for market loading (--graphql flag)[/yellow]")
        else:
            console.print("  [green]Using RPC/Core Markets for market loading (default)[/green]")

        gmx = GMX(params=gmx_params)

        # Load markets
        console.print("  Loading markets...")
        gmx.load_markets()
        console.print(f"  Loaded {len(gmx.markets)} markets")

        # ========================================================================
        # STEP 5: Create CCXT Order with SL/TP
        # ========================================================================
        console.print("\n[bold]Creating CCXT order with SL/TP...[/bold]")

        # Market selection based on CLI flag
        if args.btc:
            symbol = "BTC/USDC:USDC"
            collateral_symbol = "USDC"
        else:
            symbol = "ETH/USDC:USDC"
            collateral_symbol = "ETH"

        leverage = 2.5
        size_usd = args.size
        stop_loss_percent = args.stop_loss
        take_profit_percent = args.take_profit
        mode = args.mode

        console.print(f"  Market: {symbol}")
        console.print(f"  Collateral: {collateral_symbol}")
        console.print(f"  Size: ${size_usd} at {leverage}x leverage")
        console.print(f"  Direction: LONG")
        console.print(f"  Mode: {mode}")

        # Build params
        params = {
            "leverage": leverage,
            "collateral_symbol": collateral_symbol,
            "slippage_percent": 0.005,
            "execution_buffer": EXECUTION_BUFFER,
        }

        # Add SL/TP based on mode
        if mode in ["both", "sl-only"]:
            params["stopLoss"] = {
                "triggerPercent": stop_loss_percent,
                "closePercent": 1.0,
            }
            console.print(f"  Stop Loss: {stop_loss_percent * 100:.1f}% below entry")

        if mode in ["both", "tp-only"]:
            params["takeProfit"] = {
                "triggerPercent": take_profit_percent,
                "closePercent": 1.0,
            }
            console.print(f"  Take Profit: {take_profit_percent * 100:.1f}% above entry")

        # Create order
        order = gmx.create_market_buy_order(
            symbol,
            size_usd,
            params,
        )

        console.print(f"\n[green]Order created[/green]")
        console.print(f"  Status: {order.get('status', 'unknown')}")
        console.print(f"  TX Hash: {order.get('id', 'N/A')}")

        # Extract info
        info = order.get("info", {})
        block_num = info.get("block_number") or info.get("blockNumber")
        if block_num:
            console.print(f"  Block: {block_num}")

        # ========================================================================
        # STEP 6: Execute Order as Keeper
        # ========================================================================
        tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
        if tx_hash:
            console.print(f"\n[bold]Executing order as keeper...[/bold]")

            # Convert tx_hash to bytes
            if isinstance(tx_hash, str):
                tx_hash_bytes = bytes.fromhex(tx_hash[2:]) if tx_hash.startswith("0x") else bytes.fromhex(tx_hash)
            else:
                tx_hash_bytes = tx_hash

            receipt = web3.eth.wait_for_transaction_receipt(tx_hash_bytes)

            if receipt["status"] == 1:
                console.print(f"[green]Order transaction successful[/green]")

                # Extract order keys
                order_keys = verify_orders_created(receipt)
                if order_keys:
                    console.print(f"\n[bold]Orders Created:[/bold]")
                    for idx, key in enumerate(order_keys, 1):
                        order_type = ["Main", "Stop Loss", "Take Profit"][idx - 1] if idx <= 3 else f"Order {idx}"
                        console.print(f"  {order_type}: {key.hex()}")

                    # Execute main order
                    main_order_key = order_keys[0]
                    console.print(f"\n[bold]Executing main order as keeper...[/bold]")
                    try:
                        exec_receipt, keeper_address = execute_order_as_keeper(web3, main_order_key)
                        console.print(f"[green]Main order executed[/green]")
                        console.print(f"  Keeper: {keeper_address}")
                        console.print(f"  Block: {exec_receipt['blockNumber']}")
                        console.print(f"  Gas used: {exec_receipt['gasUsed']}")
                    except Exception as e:
                        console.print(f"[red]Keeper execution failed: {e}[/red]")
                        import traceback

                        traceback.print_exc()
            else:
                console.print(f"[red]Order transaction failed[/red]")
                assert_transaction_success_with_explanation(web3, tx_hash)

        # ========================================================================
        # STEP 7: Verify Position
        # ========================================================================
        console.print("\n[bold]Verifying position...[/bold]")
        time.sleep(2)  # Brief wait for state to settle

        config = GMXConfig(web3, user_wallet_address=wallet_address)
        position_verifier = GetOpenPositions(config)
        open_positions = position_verifier.get_data(wallet_address)

        if open_positions:
            console.print(f"[green]Found {len(open_positions)} position(s)[/green]\n")

            for idx, (position_key, position) in enumerate(open_positions.items(), 1):
                market_symbol = position.get("market_symbol", "Unknown")
                is_long = position.get("is_long", False)
                direction = "LONG" if is_long else "SHORT"
                collateral_token = position.get("collateral_token", "Unknown")

                position_size = position.get("position_size", 0)
                position_size_usd_raw = position.get("position_size_usd_raw", 0)
                initial_collateral_amount = position.get("initial_collateral_amount", 0)
                initial_collateral_amount_usd = position.get("initial_collateral_amount_usd", 0)
                entry_price = position.get("entry_price", 0)
                mark_price = position.get("mark_price", 0)
                leverage = position.get("leverage", 0)
                percent_profit = position.get("percent_profit", 0)

                token_decimals = 18 if "ETH" in collateral_token.upper() else 6
                collateral_amount = initial_collateral_amount / (10**token_decimals)

                console.print(f"  Position #{idx}")
                console.print(f"    Market:           {market_symbol}/USD")
                console.print(f"    Direction:        {direction}")
                console.print(f"    Collateral Token: {collateral_token}")
                console.print(f"    Position Size:    ${position_size:,.2f}")
                console.print(f"    Collateral:       {collateral_amount:.6f} {collateral_token}")
                console.print(f"    Collateral Value: ${initial_collateral_amount_usd:,.2f}")
                console.print(f"    Leverage:         {leverage:.2f}x")
                console.print(f"    Entry Price:      ${entry_price:,.2f}")
                console.print(f"    Mark Price:       ${mark_price:,.2f}")

                if percent_profit != 0:
                    pnl_color = "green" if percent_profit > 0 else "red"
                    console.print(f"    PnL:              [{pnl_color}]{percent_profit:+.2f}%[/{pnl_color}]")

        else:
            console.print(f"[yellow]No positions found[/yellow]")

        # ========================================================================
        # STEP 8: Display SL/TP Details
        # ========================================================================
        console.print("\n[bold]SL/TP Order Details:[/bold]")

        sl_trigger = info.get("stop_loss_trigger_price")
        tp_trigger = info.get("take_profit_trigger_price")
        entry_price = info.get("entry_price", 0)
        total_fee = info.get("total_execution_fee", 0) / 10**18 if info.get("total_execution_fee") else 0
        main_fee = info.get("main_order_fee", 0) / 10**18 if info.get("main_order_fee") else 0
        sl_fee = info.get("stop_loss_fee", 0) / 10**18 if info.get("stop_loss_fee") else 0
        tp_fee = info.get("take_profit_fee", 0) / 10**18 if info.get("take_profit_fee") else 0

        if entry_price:
            console.print(f"  Entry Price: ${entry_price:,.2f}")

        if sl_trigger:
            pct = ((sl_trigger / entry_price - 1) * 100) if entry_price > 0 else 0
            console.print(f"  Stop Loss Trigger: ${sl_trigger:,.2f} ({pct:+.2f}%)")
            console.print(f"  Stop Loss Fee: {sl_fee:.6f} ETH")

        if tp_trigger:
            pct = ((tp_trigger / entry_price - 1) * 100) if entry_price > 0 else 0
            console.print(f"  Take Profit Trigger: ${tp_trigger:,.2f} ({pct:+.2f}%)")
            console.print(f"  Take Profit Fee: {tp_fee:.6f} ETH")

        console.print(f"\n  Main Order Fee: {main_fee:.6f} ETH")
        console.print(f"  Total Execution Fee: {total_fee:.6f} ETH")

        console.print("\n[dim]Note: SL/TP orders are pending until price triggers are met.[/dim]")

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

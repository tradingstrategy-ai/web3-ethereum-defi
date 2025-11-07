"""
GMX Order Creation Test - Fork Testing with Anvil or Tenderly

This script demonstrates GMX order creation with 3 fork testing modes:

ANVIL FORK MODE (default - script creates fork):
    export ARBITRUM_CHAIN_JSON_RPC="https://arb1.arbitrum.io/rpc"
    export PRIVATE_KEY="0x..."
    python tests/gmx/debug.py                      # Anvil fork (default)
    python tests/gmx/debug.py --fork               # Explicit Anvil fork

CUSTOM ANVIL MODE (connect to existing Anvil instance):
    # Terminal 1: Start Anvil with fork and unlocked whale addresses
    anvil --fork-url $ARBITRUM_CHAIN_JSON_RPC --fork-block-number 392496384 \
      --unlock 0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055 \
      --unlock 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336

    # Terminal 2: Run script
    export PRIVATE_KEY="0x..."
    python tests/gmx/debug.py --anvil-rpc http://127.0.0.1:8545

TENDERLY FORK MODE:
    export TD_ARB="https://virtual.arbitrum.rpc.tenderly.co/YOUR_FORK_ID"
    export PRIVATE_KEY="0x..."
    python tests/gmx/debug.py --td                 # Tenderly fork

All modes automatically fund the wallet with ETH, USDC, and WETH for testing.
"""

import os
import sys
import argparse
import time

from eth_utils import to_checksum_address

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import (
    get_token_address_normalized,
    get_contract_addresses,
)
from rich.console import Console
from web3 import Web3

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import execute_order_as_keeper, setup_mock_oracle, extract_order_key_from_receipt
import logging
from rich.logging import RichHandler

# Configure logging to show detailed output from fork_helpers
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

logger = logging.getLogger("rich")

console = Console()


def fetch_chainlink_eth_price(web3: Web3, chainlink_feed_address: str = "0x639fe6ab55c921f74e7fac1ee960c0b6293ba612") -> float:
    """Fetch current ETH price from Chainlink oracle.

    Args:
        web3: Web3 instance
        chainlink_feed_address: Chainlink ETH/USD price feed address on Arbitrum

    Returns:
        ETH price in USD (converted from 8 decimals)
    """
    console.print(f"\n[bold cyan]Fetching ETH price from Chainlink oracle...[/bold cyan]")
    console.print(f"  Oracle address: {chainlink_feed_address}")

    # Chainlink AggregatorV3Interface ABI (only latestRoundData function)
    chainlink_abi = [{"inputs": [], "name": "latestRoundData", "outputs": [{"name": "roundId", "type": "uint80"}, {"name": "answer", "type": "int256"}, {"name": "startedAt", "type": "uint256"}, {"name": "updatedAt", "type": "uint256"}, {"name": "answeredInRound", "type": "uint80"}], "stateMutability": "view", "type": "function"}]

    # Create contract instance
    price_feed = web3.eth.contract(address=to_checksum_address(chainlink_feed_address), abi=chainlink_abi)

    # Fetch latest round data
    round_data = price_feed.functions.latestRoundData().call()
    round_id, answer, started_at, updated_at, answered_in_round = round_data

    console.print(f"  Round ID: {round_id}")
    console.print(f"  Answer (raw): {answer}")
    console.print(f"  Updated At: {updated_at}")

    # Chainlink ETH/USD uses 8 decimals
    # Example: 323165051034 / (10 ** 8) = 3231.65051034
    eth_price_usd = answer / (10**8)

    console.print(f"  [bold green]ETH Price: ${eth_price_usd:,.2f}[/bold green]")

    # Add 10% buffer for acceptable price (so orders go through)
    acceptable_price = eth_price_usd * 1.10
    console.print(f"  [bold yellow]Acceptable Price (with 10% buffer): ${acceptable_price:,.2f}[/bold yellow]")

    return eth_price_usd


def tenderly_set_balance(web3: Web3, wallet_address: str, amount_eth: float):
    """Set ETH balance on Tenderly fork using tenderly_setBalance."""
    amount_wei = int(amount_eth * 1e18)
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(amount_wei)])
    console.print(f"  [green]Set ETH balance: {amount_eth} ETH[/green]")


def tenderly_set_erc20_balance(web3: Web3, token_address: str, wallet_address: str, amount: int):
    """Set ERC20 token balance on Tenderly fork using tenderly_setErc20Balance."""
    web3.provider.make_request("tenderly_setErc20Balance", [token_address, wallet_address, hex(amount)])
    token_details = fetch_erc20_details(web3, token_address)
    formatted_amount = amount / (10**token_details.decimals)
    console.print(f"  [green]Set {token_details.symbol} balance: {formatted_amount:.2f}[/green]")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX Order Creation Test - Fork Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Fork mode options (mutually exclusive)
    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    fork_group.add_argument("--td", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    fork_group.add_argument("--anvil-rpc", type=str, help="Connect to existing Anvil RPC (e.g., http://127.0.0.1:8545)")

    # Position size override
    parser.add_argument("--size", type=float, default=10.0, help="Position size in USD (default: 10)")

    return parser.parse_args()


def main():
    """Main execution flow."""
    large_usdc_holder_arbitrum = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
    large_weth_holder_arbitrum = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")

    args = parse_arguments()

    # Get private key
    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    launch = None

    try:
        console.print("\n[bold green]=== GMX Fork Test ===[/bold green]\n")

        # ========================================================================
        # STEP 1: Connect to Network (3 modes)
        # ========================================================================

        # Mode 1: Tenderly fork
        if args.td:
            tenderly_rpc = os.environ.get("TD_ARB")
            if not tenderly_rpc:
                console.print("[red]Error: TD_ARB environment variable not set[/red]")
                console.print("[yellow]Set TD_ARB to your Tenderly fork RPC URL[/yellow]")
                sys.exit(1)

            console.print("Using Tenderly fork...")
            web3 = create_multi_provider_web3(tenderly_rpc)

            block_number = web3.eth.block_number
            chain_id = web3.eth.chain_id
            chain = get_chain_name(chain_id).lower()

            console.print(f"  Block: {block_number}")
            console.print(f"  Chain ID: {chain_id}")
            console.print(f"  Chain: {chain}")

            # Fetch ETH price from Chainlink
            eth_price = fetch_chainlink_eth_price(web3)

            # Setup mock oracle provider
            console.print("\n[dim]Setting up mock oracle provider...[/dim]")
            setup_mock_oracle(web3, eth_price_usd=eth_price, usdc_price_usd=1)
            console.print("[dim]✓ Mock oracle configured[/dim]\n")

        # Mode 2: Custom Anvil RPC
        elif args.anvil_rpc:
            console.print(f"Using existing Anvil instance at {args.anvil_rpc}...")
            console.print("[dim]NOTE: Make sure you started Anvil with fork and unlocked addresses:[/dim]")
            console.print("[dim]  anvil --fork-url $ARBITRUM_CHAIN_JSON_RPC --fork-block-number 392496384 \\[/dim]")
            console.print("[dim]    --unlock 0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055 \\[/dim]")
            console.print("[dim]    --unlock 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336[/dim]")

            web3 = create_multi_provider_web3(args.anvil_rpc)

            block_number = web3.eth.block_number
            chain_id = web3.eth.chain_id
            chain = get_chain_name(chain_id).lower()

            console.print(f"  Block: {block_number}")
            console.print(f"  Chain ID: {chain_id}")
            console.print(f"  Chain: {chain}")

            # Fetch ETH price from Chainlink
            eth_price = fetch_chainlink_eth_price(web3)

            # Setup mock oracle provider
            console.print("\n[dim]Setting up mock oracle provider...[/dim]")
            setup_mock_oracle(web3, eth_price_usd=eth_price, usdc_price_usd=1)
            console.print("[dim]✓ Mock oracle configured[/dim]\n")

        # Mode 3: Anvil fork (default - script creates fork)
        else:
            fork_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
            if not fork_rpc:
                console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
                sys.exit(1)

            fork_block = 392496384
            console.print(f"Creating Anvil fork at block {fork_block}...")

            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[
                    large_usdc_holder_arbitrum,
                    large_weth_holder_arbitrum,
                ],
                fork_block_number=fork_block,
            )
            web3 = Web3(Web3.HTTPProvider(launch.json_rpc_url))

            block_number = web3.eth.block_number
            chain_id = web3.eth.chain_id
            chain = get_chain_name(chain_id).lower()

            console.print(f"  Anvil fork started on {launch.json_rpc_url}")
            console.print(f"  Block: {block_number}")
            console.print(f"  Chain ID: {chain_id}")
            console.print(f"  Chain: {chain}")

            # Fetch ETH price from Chainlink
            eth_price = fetch_chainlink_eth_price(web3)

            # Setup mock oracle provider
            console.print("\n[dim]Setting up mock oracle provider...[/dim]")
            setup_mock_oracle(web3, eth_price_usd=eth_price, usdc_price_usd=1)
            console.print("[dim]✓ Mock oracle configured[/dim]\n")

        # ========================================================================
        # STEP 2: Setup Wallet
        # ========================================================================
        console.print("\n[bold]Setting up wallet...[/bold]")
        wallet = HotWallet.from_private_key(private_key)
        wallet.sync_nonce(web3)
        wallet_address = wallet.get_main_address()
        console.print(f"  Wallet: {wallet_address}")

        # Get token addresses
        tokens = {
            "WETH": get_token_address_normalized(chain, "WETH"),
            "USDC": get_token_address_normalized(chain, "USDC"),
        }
        for symbol, address in tokens.items():
            console.print(f"  {symbol}: {address}")

        # ========================================================================
        # STEP 2.5: Fund Wallet (all fork modes)
        # ========================================================================
        console.print("\n[bold]Funding wallet...[/bold]")

        if args.td:
            # Tenderly fork - use Tenderly RPC calls
            tenderly_set_balance(web3, wallet_address, 100.0)

            usdc_address = tokens.get("USDC")
            if usdc_address:
                usdc_amount = 100_000 * (10**6)  # 100k USDC
                tenderly_set_erc20_balance(web3, usdc_address, wallet_address, usdc_amount)

            weth_address = tokens.get("WETH")
            if weth_address:
                weth_amount = 1000 * (10**18)  # 1000 WETH
                tenderly_set_erc20_balance(web3, weth_address, wallet_address, weth_amount)

        else:
            # Anvil fork (both modes) - use anvil_setBalance + transfers from unlocked addresses
            eth_amount_wei = 100 * 10**18  # 100 ETH
            web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
            console.print(f"  [green]Set ETH balance: 100 ETH[/green]")

            # Set USDC balance via transfer from unlocked whale
            usdc_address = tokens.get("USDC")
            if usdc_address:
                usdc_amount = 100_000 * (10**6)  # 100k USDC
                usdc_token = fetch_erc20_details(web3, usdc_address)
                usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": large_usdc_holder_arbitrum})
                balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
                console.print(f"  [green]Set USDC balance: {balance / 10**6:.2f} USDC[/green]")

            # Set WETH balance via transfer from unlocked whale
            weth_address = tokens.get("WETH")
            if weth_address:
                weth_amount = 1000 * (10**18)  # 1000 WETH
                weth_token = fetch_erc20_details(web3, weth_address)
                weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": large_weth_holder_arbitrum})
                balance = weth_token.contract.functions.balanceOf(wallet_address).call()
                console.print(f"  [green]Set WETH balance: {balance / 10**18:.2f} WETH[/green]")

        # ========================================================================
        # STEP 3: Configure Position Parameters
        # ========================================================================
        console.print("\n[bold]Creating GMX order...[/bold]")

        # Determine position size
        size_usd = args.size

        # Configure position
        market_symbol = "ETH"
        collateral_symbol = "ETH"  # ETH gets auto-wrapped to WETH by GMX
        start_token_symbol = "ETH"
        leverage = 2.5

        console.print(f"  Market: {market_symbol}")
        console.print(f"  Collateral: {collateral_symbol}")
        console.print(f"  Size: ${size_usd} at {leverage}x leverage")
        console.print(f"  Direction: LONG")

        # ========================================================================
        # STEP 4: Create GMX Order
        # ========================================================================

        config = GMXConfig(web3, user_wallet_address=wallet_address)
        trading_client = GMXTrading(config)

        order = trading_client.open_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=start_token_symbol,
            is_long=True,
            size_delta_usd=size_usd,
            leverage=leverage,
            slippage_percent=0.005,
            execution_buffer=2.2,
        )

        console.print(f"\n[green]Order object created successfully![/green]")
        console.print(f"  Execution Fee: {order.execution_fee / 1e18:.6f} ETH")
        console.print(f"  Mark Price: {order.mark_price}")
        console.print(f"  Gas Limit: {order.gas_limit}")

        # ========================================================================
        # STEP 5: Handle Token Approval
        # ========================================================================
        console.print("\n[bold]Handling token approval...[/bold]")

        collateral_token_address = get_token_address_normalized(chain, collateral_symbol)

        # ETH doesn't need approval (native currency, will be wrapped by ExchangeRouter)
        if collateral_token_address is None or collateral_symbol in ["ETH", "WETH"]:
            console.print(f"  [green]Using native ETH - no approval needed[/green]")
        else:
            token_details = fetch_erc20_details(web3, collateral_token_address)
            token_contract = token_details.contract

            contract_addresses = get_contract_addresses(chain)
            spender_address = contract_addresses.syntheticsrouter

            current_allowance = token_contract.functions.allowance(wallet_address, spender_address).call()
            required_amount = 1_000_000_000 * (10**token_details.decimals)

            console.print(f"  Current allowance: {current_allowance / (10**token_details.decimals):.6f} {collateral_symbol}")

            if current_allowance < required_amount:
                console.print(f"  Approving {collateral_symbol}...")

                approve_tx = token_contract.functions.approve(spender_address, required_amount).build_transaction(
                    {
                        "from": wallet_address,
                        "gas": 100000,
                        "gasPrice": web3.eth.gas_price,
                    }
                )

                if "nonce" in approve_tx:
                    del approve_tx["nonce"]

                signed_approve_tx = wallet.sign_transaction_with_new_nonce(approve_tx)
                approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)

                console.print(f"    TX: {approve_tx_hash.hex()}")
                approve_receipt = web3.eth.wait_for_transaction_receipt(approve_tx_hash)
                console.print(f"  [green]Approval successful[/green]")
            else:
                console.print(f"  [green]Sufficient allowance exists[/green]")

        # ========================================================================
        # STEP 6: Submit Order
        # ========================================================================
        console.print("\n[bold]Submitting order to ExchangeRouter...[/bold]")

        transaction = order.transaction
        if "nonce" in transaction:
            del transaction["nonce"]

        console.print(f"  To: {transaction['to']}")
        console.print(f"  Value: {transaction['value'] / 1e18:.6f} ETH")
        console.print(f"  Data size: {len(transaction['data'])} bytes")

        signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        console.print(f"\n  TX Hash: {tx_hash.hex()}")

        # Wait for confirmation
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        console.print(f"\n[bold]Transaction Status: {receipt['status']}[/bold]")

        if receipt["status"] == 1:
            console.print(f"[green]✓ Order submitted successfully![/green]")
            console.print(f"  Block: {receipt['blockNumber']}")
            console.print(f"  Gas used: {receipt['gasUsed']}")

            # ========================================================================
            # STEP 6.5: Extract Order Key from Receipt
            # ========================================================================
            order_key = None
            try:
                order_key = extract_order_key_from_receipt(receipt)
                console.print(f"\n[green]✓ Order Key: {order_key.hex()}[/green]")
            except Exception as e:
                console.print(f"\n[yellow]Warning: Could not extract order key: {e}[/yellow]")

            # ========================================================================
            # STEP 7: Execute Order as Keeper (Fork mode)
            # ========================================================================
            if order_key:
                console.print("\n[bold]Executing order as keeper...[/bold]")
                try:
                    exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)

                    console.print(f"[green]✓ Order executed successfully![/green]")
                    console.print(f"  Keeper: {keeper_address}")
                    console.print(f"  Block: {exec_receipt['blockNumber']}")
                    console.print(f"  Gas used: {exec_receipt['gasUsed']}")

                except Exception as e:
                    console.print(f"[red]✗ Keeper execution failed: {e}[/red]")
                    import traceback

                    console.print(f"[dim]{traceback.format_exc()}[/dim]")

            # ========================================================================
            # STEP 8: Verify Position is Opened
            # ========================================================================
            console.print("\n[bold]Sleeping for 10s bcz why not...[/bold]")
            time.sleep(7)
            console.print("\n[bold]Verifying position...[/bold]")

            # Check if position is opened
            position_verifier = GetOpenPositions(config)
            open_positions = position_verifier.get_data(wallet_address)

            if open_positions:
                console.print(f"[green]✓ Position opened successfully![/green]")
                console.print(f"  Found {len(open_positions)} open position(s):")

                for position_key, position in open_positions.items():
                    console.print(f"\n  Position: {position_key}")
                    console.print(f"    Market: {position.get('market_symbol', 'N/A')}")
                    console.print(f"    Direction: {'LONG' if position.get('is_long') else 'SHORT'}")
                    console.print(f"    Size: ${position.get('position_size', 0):,.2f}")
                    console.print(f"    Collateral: ${position.get('collateral_usd', 0):,.2f}")
                    console.print(f"    Entry Price: ${position.get('entry_price', 0):,.2f}")

                    # Calculate PnL if available
                    pnl = position.get("pnl_usd", 0)
                    if pnl != 0:
                        pnl_color = "green" if pnl > 0 else "red"
                        console.print(f"    PnL: [{pnl_color}]${pnl:,.2f}[/{pnl_color}]")
            else:
                console.print(f"[yellow]⚠ No open positions found for wallet {wallet_address}[/yellow]")
                console.print("[dim]Order may not have been executed by keeper yet.[/dim]")

        else:
            console.print(f"\n[red]✗ Order transaction failed[/red]")
            console.print(f"  Status: {receipt['status']}")

            # Try to get revert reason
            try:
                assert_transaction_success_with_explanation(web3, tx_hash)
            except Exception as e:
                console.print(f"  Error: {str(e)}")

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

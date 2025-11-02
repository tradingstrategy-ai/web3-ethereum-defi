"""
GMX Order Creation Test - Supports Fork and Real RPC

This script demonstrates GMX order creation with flexible network options:
1. Fork mode: Create orders on Anvil/Tenderly fork (uses fake balances)
2. Real RPC mode: Create orders on Arbitrum mainnet/testnet (uses real money!)

ANVIL FORK MODE (safe testing with fake balances):
    export ARBITRUM_CHAIN_JSON_RPC="https://arb1.arbitrum.io/rpc"
    export PRIVATE_KEY="0x..."

    python tests/gmx/debug.py                      # Anvil fork (default)

TENDERLY FORK MODE (safe testing with Tenderly):
    export TD_ARB="https://virtual.arbitrum.rpc.tenderly.co/YOUR_FORK_ID"
    export PRIVATE_KEY="0x..."

    python tests/gmx/debug.py --td                 # Tenderly fork
    python tests/gmx/debug.py --fork-provider tenderly

REAL RPC MODE (uses real money - be careful!):
    export PRIVATE_KEY="0x..."

    python tests/gmx/debug.py --mainnet            # Arbitrum mainnet
    python tests/gmx/debug.py --sp                 # Arbitrum Sepolia testnet
    python tests/gmx/debug.py --mainnet --size 1   # Custom position size
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
from eth_defi.utils import addr
from tests.gmx.conftest import large_usdc_holder_arbitrum, large_weth_holder_arbitrum
from tests.gmx.setup_test.fork_helpers import set_erc20_balance, set_eth_balance
from tests.guard.test_guard_simple_vault_aave_v3 import large_usdc_holder

console = Console()


def tenderly_set_balance(web3: Web3, wallet_address: str, amount_eth: float):
    """Set ETH balance on Tenderly fork using tenderly_setBalance."""
    amount_wei = int(amount_eth * 1e18)
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(amount_wei)])
    console.print(f"  [green]Set ETH balance: {amount_eth} ETH[/green]")


def tenderly_set_erc20_balance(web3: Web3, token_address: str, wallet_address: str, amount: int):
    """Set ERC20 token balance on Tenderly fork using tenderly_addErc20Balance."""
    web3.provider.make_request("tenderly_addErc20Balance", [token_address, [wallet_address], hex(amount)])
    token_details = fetch_erc20_details(web3, token_address)
    formatted_amount = amount / (10**token_details.decimals)
    console.print(f"  [green]Set {token_details.symbol} balance: {formatted_amount:.2f}[/green]")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX Order Creation Test (Fork or Real RPC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Fork provider options
    parser.add_argument("--fork-provider", choices=["anvil", "tenderly"], default="anvil", help="Fork provider to use (default: anvil)")
    parser.add_argument("--td", action="store_const", const="tenderly", dest="fork_provider", help="Shorthand for --fork-provider tenderly")

    # Real RPC options
    parser.add_argument("--mainnet", action="store_true", help="Run on REAL Arbitrum mainnet (uses real money!)")
    parser.add_argument("--arbitrum-sepolia", action="store_true", help="Run on Arbitrum Sepolia testnet")
    parser.add_argument("--sp", action="store_const", const=True, dest="arbitrum_sepolia", help="Shorthand for --arbitrum-sepolia")

    # Position size override
    parser.add_argument("--size", type=float, default=None, help="Position size in USD (default: 10 for fork, 1 for real RPC)")

    return parser.parse_args()


def main():
    """Main execution flow."""
    large_arb_holder_arbitrum = to_checksum_address("0xF977814e90dA44bFA03b6295A0616a897441aceC")
    large_usdc_holder_arbitrum = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
    large_weth_holder_arbitrum = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")

    args = parse_arguments()
    use_real_rpc = args.mainnet or args.arbitrum_sepolia

    # Get private key
    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    launch = None

    try:
        # ========================================================================
        # STEP 1: Connect to Network
        # ========================================================================
        if use_real_rpc:
            console.print("\n[bold yellow]=== GMX Real RPC Test ===[/bold yellow]\n")

            if args.mainnet:
                rpc_url = os.environ.get("ARBITRUM_CHAIN_JSON_RPC") or os.environ.get("ARBITRUM_RPC_URL")
                if not rpc_url:
                    console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC or ARBITRUM_RPC_URL not set[/red]")
                    sys.exit(1)
                console.print("Connecting to [bold]Arbitrum Mainnet[/bold]...")
                console.print("[yellow]WARNING: This uses REAL MONEY![/yellow]")
            else:
                rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL") or "https://sepolia-rollup.arbitrum.io/rpc"
                console.print("Connecting to [bold]Arbitrum Sepolia Testnet[/bold]...")

            # console.print(f"  RPC: {rpc_url}")
            web3 = create_multi_provider_web3(rpc_url)

            block_number = web3.eth.block_number
            chain_id = web3.eth.chain_id
            chain = get_chain_name(chain_id).lower()

            console.print(f"  Block: {block_number}")
            console.print(f"  Chain ID: {chain_id}")
            console.print(f"  Chain: {chain}")

        else:
            # Fork mode
            console.print("\n[bold green]=== GMX Fork Test ===[/bold green]\n")

            if args.fork_provider == "tenderly":
                # Tenderly fork mode
                tenderly_rpc = os.environ.get("TD_ARB")
                if not tenderly_rpc:
                    console.print("[red]Error: TD_ARB environment variable not set[/red]")
                    console.print("[yellow]Set TD_ARB to your Tenderly fork RPC URL[/yellow]")
                    sys.exit(1)

                console.print(f"Using Tenderly fork...")

                web3 = create_multi_provider_web3(tenderly_rpc)

                block_number = web3.eth.block_number
                chain_id = web3.eth.chain_id
                chain = get_chain_name(chain_id).lower()

                console.print(f"  Block: {block_number}")
                console.print(f"  Chain ID: {chain_id}")
                console.print(f"  Chain: {chain}")

            else:
                # Anvil fork mode
                fork_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
                if not fork_rpc:
                    console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
                    sys.exit(1)

                console.print(f"Forking Arbitrum mainnet with Anvil...")
                # console.print(f"  RPC: {fork_rpc}")

                launch = fork_network_anvil(
                    fork_rpc,
                    unlocked_addresses=[
                        large_arb_holder_arbitrum,
                        large_usdc_holder_arbitrum,
                        large_weth_holder_arbitrum,
                    ],
                )
                web3 = Web3(Web3.HTTPProvider(launch.json_rpc_url))

                block_number = web3.eth.block_number
                chain_id = web3.eth.chain_id
                chain = get_chain_name(chain_id).lower()

                console.print(f"  Anvil fork started on {launch.json_rpc_url}")
                console.print(f"  Block: {block_number}")
                console.print(f"  Chain ID: {chain_id}")
                console.print(f"  Chain: {chain}")

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

        # Fund wallet if using fork mode
        if not use_real_rpc:
            if args.fork_provider == "tenderly":
                console.print("\n[bold]Funding wallet on Tenderly fork...[/bold]")

                # Set ETH balance
                tenderly_set_balance(web3, wallet_address, 100.0)

                # Set USDC balance (if available)
                usdc_address = tokens.get("USDC")
                if usdc_address:
                    usdc_amount = 100_000 * (10**6)  # 100k USDC (6 decimals)
                    tenderly_set_erc20_balance(web3, usdc_address, wallet_address, usdc_amount)

                # Set WETH balance (if available)
                weth_address = tokens.get("WETH")
                if weth_address:
                    weth_amount = 1000 * (10**18)  # 1000 WETH (18 decimals)
                    tenderly_set_erc20_balance(web3, weth_address, wallet_address, weth_amount)

            else:
                # Anvil fork mode - fund using storage manipulation
                console.print("\n[bold]Funding wallet on Anvil fork...[/bold]")
                try:
                    # 1. Set ETH balance using anvil_setBalance
                    eth_amount_wei = 100 * 10**18  # 100 ETH
                    set_eth_balance(web3, wallet_address, eth_amount_wei)
                    console.print(f"  [green]Set ETH balance: 100 ETH[/green]")

                    # 2. Set USDC balance using storage manipulation
                    usdc_address = tokens.get("USDC")
                    if usdc_address:
                        usdc_amount = 100_000 * (10**6)  # 100k USDC (6 decimals)
                        usdc_token = fetch_erc20_details(web3, usdc_address)
                        usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact(
                            {"from": large_usdc_holder_arbitrum},
                        )
                        # Verify balance
                        balance = usdc_token.contract.functions.balanceOf(
                            wallet_address,
                        ).call()
                        console.print(f"  [green]Set USDC balance: {balance / 10**6:.2f} USDC[/green]")

                    # 3. Set WETH balance using storage manipulation
                    weth_address = tokens.get("WETH")
                    if weth_address:
                        weth_amount = 1000 * (10**18)  # 1000 WETH (18 decimals)
                        weth_token = fetch_erc20_details(web3, weth_address)

                        weth_token.contract.functions.transfer(wallet_address, weth_amount).transact(
                            {"from": large_weth_holder_arbitrum},
                        )

                        # Verify balance
                        balance = weth_token.contract.functions.balanceOf(wallet_address).call()
                        console.print(f"  [green]Set WETH balance: {balance / 10**18:.2f} WETH[/green]")

                except ImportError as e:
                    console.print(f"  [yellow]Warning: Fork helpers not available: {e}[/yellow]")
                    console.print(f"  [dim]Falling back to anvil_setBalance for ETH only[/dim]")

                    # Fallback: just set ETH balance
                    eth_amount_wei = 100 * 10**18
                    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
                    console.print(f"  [green]Set ETH balance: 100 ETH[/green]")

        # ========================================================================
        # STEP 3: Configure Position Parameters
        # ========================================================================
        console.print("\n[bold]Creating GMX order...[/bold]")

        # Determine position size
        if args.size:
            size_usd = args.size
        else:
            size_usd = 1.0 if use_real_rpc else 10.0

        # Configure position - using working parameters from gmx_open_position.py
        if chain == "arbitrum_sepolia":
            market_symbol = "CRV"
            collateral_symbol = "USDC.SG"
            start_token_symbol = "USDC.SG"
            leverage = 1.0  # Match working script
        else:
            market_symbol = "ETH"
            collateral_symbol = "USDC"
            start_token_symbol = "USDC"
            leverage = 1.5

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

        if receipt["status"] == 1:
            console.print(f"\n[green]✓ Order submitted successfully![/green]")
            console.print(f"  Block: {receipt['blockNumber']}")
            console.print(f"  Gas used: {receipt['gasUsed']}")

            # ========================================================================
            # STEP 7: Execute Order as Keeper (Anvil Fork Only)
            # ========================================================================
            if not use_real_rpc and args.fork_provider == "anvil":
                console.print("\n[bold]Executing order as keeper on Anvil fork...[/bold]")

                try:
                    # Import keeper execution utilities
                    from tests.gmx.setup_test.event_parser import extract_order_key_from_receipt
                    from tests.gmx.setup_test.keeper_executor import execute_order_as_keeper

                    # Extract order key from receipt
                    order_key = extract_order_key_from_receipt(receipt)
                    console.print(f"  Order key: {order_key.hex()}")

                    # Get current oracle prices for execution
                    # In real scenarios, keepers fetch these from oracle providers
                    # For testing, we use reasonable prices
                    from eth_defi.gmx.core.oracle import OraclePrices

                    oracle = OraclePrices(config)
                    prices = oracle.get_recent_prices()

                    # Get ETH and USDC prices from oracle
                    weth_address = get_token_address_normalized(chain, "WETH")
                    usdc_address = get_token_address_normalized(chain, "USDC")

                    eth_price = int((prices[weth_address]["min"] + prices[weth_address]["max"]) / 2)
                    usdc_price = int((prices[usdc_address]["min"] + prices[usdc_address]["max"]) / 2)

                    console.print(f"  Using oracle prices: ETH=${eth_price:,.2f}, USDC=${usdc_price:.4f}")

                    # Execute order as keeper using the Python keeper executor
                    # This uses anvil_impersonateAccount to execute as the keeper
                    exec_receipt = execute_order_as_keeper(
                        web3=web3,
                        order_key=order_key,
                        chain=chain,
                        eth_price_usd=eth_price,
                        usdc_price_usd=usdc_price,
                    )

                    if exec_receipt["status"] == 1:
                        console.print(f"  [green]✓ Order executed by keeper[/green]")
                        console.print(f"  Execution Block: {exec_receipt['blockNumber']}")
                        console.print(f"  Execution Gas: {exec_receipt['gasUsed']}")
                    else:
                        console.print(f"  [red]✗ Keeper execution failed[/red]")
                        try:
                            assert_transaction_success_with_explanation(web3, exec_receipt["transactionHash"])
                        except Exception as revert_error:
                            console.print(f"  Revert reason: {str(revert_error)}")

                except ImportError:
                    console.print(f"  [yellow]Warning: Keeper executor module not available[/yellow]")
                    console.print(f"  [dim]Install test dependencies or keeper execution will be skipped[/dim]")
                except Exception as keeper_error:
                    console.print(f"  [yellow]Warning: Could not execute as keeper: {str(keeper_error)}[/yellow]")
                    import traceback

                    console.print(f"  [dim]{traceback.format_exc()}[/dim]")

            # ========================================================================
            # STEP 8: Verify Position is Opened
            # ========================================================================
            console.print("\n[bold]Verifying position...[/bold]")

            if use_real_rpc:
                console.print("[yellow]Note: GMX keepers will execute your order within a few minutes[/yellow]")
                console.print("[yellow]Waiting 30 seconds for keeper execution...[/yellow]")
                time.sleep(30)

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
                if use_real_rpc:
                    console.print("[dim]Position may still be executing. Check GMX interface or wait longer.[/dim]")
                else:
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

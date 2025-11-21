"""GMX Swap Order Test - Fork Testing

Creates and executes GMX swap orders on forked networks for testing.

MODES:
1. Anvil fork (default):   python tests/gmx/debug_swap.py --fork
2. Tenderly fork:          python tests/gmx/debug_swap.py --td
3. Custom Anvil RPC:       python tests/gmx/debug_swap.py --anvil-rpc http://localhost:8545

Required environment variables:
- PRIVATE_KEY: Private key for signing transactions
- ARBITRUM_CHAIN_JSON_RPC: RPC endpoint for Anvil fork
- TD_ARB: Tenderly fork URL (for --td mode)
"""

import os
import sys
import argparse
import time

from eth_utils import to_checksum_address

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import get_token_address_normalized, get_contract_addresses
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

# Fork test configuration
FORK_BLOCK = 392496384
LARGE_USDC_HOLDER = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
LARGE_WETH_HOLDER = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")


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
    console.print(f"[dim]✓ Mock oracle configured with on-chain prices[/dim]\n")

    return chain


def fund_wallet_anvil(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Anvil fork using anvil_setBalance and whale transfers."""
    console.print("\n[bold]Funding wallet (Anvil mode)...[/bold]")

    # Set ETH balance for wallet
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]✓ ETH balance: 100 ETH[/green]")

    # Give whales some ETH for gas
    gas_eth = 1 * 10**18
    web3.provider.make_request("anvil_setBalance", [LARGE_USDC_HOLDER, hex(gas_eth)])
    web3.provider.make_request("anvil_setBalance", [LARGE_WETH_HOLDER, hex(gas_eth)])

    # Transfer USDC from whale
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        usdc_token = fetch_erc20_details(web3, usdc_address)
        usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": LARGE_USDC_HOLDER})
        balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]✓ USDC balance: {balance / 10**6:.2f} USDC[/green]")

    # Transfer WETH from whale
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        weth_token = fetch_erc20_details(web3, weth_address)
        weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": LARGE_WETH_HOLDER})
        balance = weth_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]✓ WETH balance: {balance / 10**18:.2f} WETH[/green]")


def fund_wallet_tenderly(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Tenderly fork using Tenderly RPC methods."""
    console.print("\n[bold]Funding wallet (Tenderly mode)...[/bold]")

    # Set ETH balance
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]✓ ETH balance: 100 ETH[/green]")

    # Set USDC balance
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        web3.provider.make_request("tenderly_setErc20Balance", [usdc_address, wallet_address, hex(usdc_amount)])
        console.print(f"  [green]✓ USDC balance: 100,000 USDC[/green]")

    # Set WETH balance
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        web3.provider.make_request("tenderly_setErc20Balance", [weth_address, wallet_address, hex(weth_amount)])
        console.print(f"  [green]✓ WETH balance: 1,000 WETH[/green]")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX Swap Order Test - Fork Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Fork mode options (mutually exclusive)
    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    fork_group.add_argument("--td", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    fork_group.add_argument("--anvil-rpc", type=str, help="Connect to existing Anvil RPC (e.g., http://127.0.0.1:8545)")

    # Swap parameters
    parser.add_argument("--amount", type=float, default=100.0, help="Amount to swap (default: 100)")
    parser.add_argument("--direction", type=str, choices=["usdc-to-eth", "eth-to-usdc"], default="usdc-to-eth", help="Swap direction (default: usdc-to-eth)")

    return parser.parse_args()


def main():
    """Main execution flow."""
    args = parse_arguments()

    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    launch = None
    is_tenderly = False

    try:
        console.print("\n[bold green]=== GMX Swap Fork Test ===[/bold green]\n")

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

            console.print(f"Creating Anvil fork at block {FORK_BLOCK}...")
            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[LARGE_USDC_HOLDER, LARGE_WETH_HOLDER],
                fork_block_number=FORK_BLOCK,
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
        # STEP 3: Fund Wallet
        # ========================================================================
        if is_tenderly:
            fund_wallet_tenderly(web3, wallet_address, tokens)
        else:
            fund_wallet_anvil(web3, wallet_address, tokens)

        # ========================================================================
        # STEP 4: Check Initial Balances
        # ========================================================================
        console.print("\n[bold]Initial Balances:[/bold]")

        usdc_token = fetch_erc20_details(web3, tokens["USDC"])
        weth_token = fetch_erc20_details(web3, tokens["WETH"])

        initial_usdc = usdc_token.contract.functions.balanceOf(wallet_address).call()
        initial_weth = weth_token.contract.functions.balanceOf(wallet_address).call()
        initial_eth = web3.eth.get_balance(wallet_address)

        console.print(f"  USDC: {initial_usdc / 10**6:.2f}")
        console.print(f"  WETH: {initial_weth / 10**18:.6f}")
        console.print(f"  ETH:  {initial_eth / 10**18:.6f}")

        # ========================================================================
        # STEP 5: Approve Tokens for GMX Synthetics Router
        # ========================================================================
        console.print("\n[bold]Approving tokens for GMX Synthetics Router...[/bold]")

        # Get the synthetics router address - this is the contract that needs token approval
        contract_addresses = get_contract_addresses(chain)
        router_address = contract_addresses.syntheticsrouter
        console.print(f"  Synthetics Router: {router_address}")

        # Approve USDC
        max_approval = 2**256 - 1
        usdc_allowance = usdc_token.contract.functions.allowance(wallet_address, router_address).call()
        if usdc_allowance < initial_usdc:
            approve_tx = usdc_token.contract.functions.approve(router_address, max_approval).build_transaction(
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
            approve_receipt = web3.eth.wait_for_transaction_receipt(approve_hash)
            assert approve_receipt["status"] == 1, "USDC approval failed"
            console.print(f"  [green]✓ USDC approved[/green]")
        else:
            console.print(f"  [dim]USDC already approved[/dim]")

        # Approve WETH
        weth_allowance = weth_token.contract.functions.allowance(wallet_address, router_address).call()
        if weth_allowance < initial_weth:
            approve_tx = weth_token.contract.functions.approve(router_address, max_approval).build_transaction(
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
            approve_receipt = web3.eth.wait_for_transaction_receipt(approve_hash)
            assert approve_receipt["status"] == 1, "WETH approval failed"
            console.print(f"  [green]✓ WETH approved[/green]")
        else:
            console.print(f"  [dim]WETH already approved[/dim]")

        # ========================================================================
        # STEP 6: Create and Submit GMX Swap Order
        # ========================================================================
        console.print("\n[bold]Creating GMX swap order...[/bold]")

        config = GMXConfig(web3, user_wallet_address=wallet_address)
        trading_client = GMXTrading(config)

        if args.direction == "usdc-to-eth":
            in_token = "USDC"
            out_token = "ETH"  # GMX uses "ETH" as symbol
            swap_amount = args.amount
            console.print(f"  Swap: {swap_amount:.2f} USDC -> ETH")
        else:
            in_token = "ETH"  # GMX uses "ETH" as symbol
            out_token = "USDC"
            swap_amount = args.amount
            console.print(f"  Swap: {swap_amount:.6f} ETH -> USDC")

        order = trading_client.swap_tokens(
            in_token_symbol=in_token,
            out_token_symbol=out_token,
            amount=swap_amount,
            slippage_percent=0.03,
            execution_buffer=5.0,
        )

        console.print(f"\n[green]✓ Swap order created[/green]")
        console.print(f"  Execution Fee: {order.execution_fee / 1e18:.6f} ETH")
        if hasattr(order, "mark_price"):
            console.print(f"  Mark Price: {order.mark_price}")
        # Print all order attributes for debugging
        console.print(f"  Order attributes: {[attr for attr in dir(order) if not attr.startswith('_')]}")

        console.print("\n[bold]Submitting swap order...[/bold]")

        transaction = order.transaction
        if "nonce" in transaction:
            del transaction["nonce"]

        signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        console.print(f"  TX Hash: {tx_hash.hex()}")

        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt["status"] == 1:
            console.print(f"[green]✓ Swap order submitted[/green]")
            console.print(f"  Block: {receipt['blockNumber']}")
            console.print(f"  Gas used: {receipt['gasUsed']}")

            # ========================================================================
            # STEP 7: Execute Order as Keeper
            # ========================================================================
            order_key = None
            try:
                order_key = extract_order_key_from_receipt(receipt)
                console.print(f"\n[green]✓ Order Key: {order_key.hex()}[/green]")
                # Debug: Show all logs with OrderCreated events
                for i, log in enumerate(receipt.get("logs", [])):
                    topics = log.get("topics", [])
                    if len(topics) >= 3:
                        topic1 = topics[1].hex() if isinstance(topics[1], bytes) else topics[1]
                        if "a7427759bfd3b941f14e687e129519da3c9b0046c5b9aaa290bb1dede63753b3" in topic1:
                            topic2 = topics[2].hex() if isinstance(topics[2], bytes) else topics[2]
                            console.print(f"  [dim]Log {i} OrderCreated key: {topic2}[/dim]")
            except Exception as e:
                console.print(f"\n[yellow]⚠ Could not extract order key: {e}[/yellow]")

            if order_key:
                console.print("\n[bold]Executing swap order as keeper...[/bold]")
                try:
                    exec_receipt, keeper_address = execute_order_as_keeper(web3, order_key)

                    console.print(f"[green]✓ Swap order executed[/green]")
                    console.print(f"  Keeper: {keeper_address}")
                    console.print(f"  Block: {exec_receipt['blockNumber']}")
                    console.print(f"  Gas used: {exec_receipt['gasUsed']}")
                    console.print(f"  Status: {exec_receipt['status']}")
                    console.print(f"  Logs count: {len(exec_receipt.get('logs', []))}")

                    # Check for OrderCancelled or other events
                    # Known event hashes (topics[1] for GMX EventLog2)
                    ORDER_EXECUTED = "bcf14314d3ff13b37dc264e85ab0c95b013fa3d55d6168b18461c45da9a3ba0f"
                    ORDER_CANCELLED = "e23b7b5c0a6a1f953c3e9f3b45f3b55e3b0f9a4d36d7b42f7e3bb6c3d72d4e35"
                    SWAP_INFO = "a79c3b54e6c385b39fd2cb5c51f42cc9a9bc5f1f7d9a6e5f7f3a5d4e5b6c7d8e"  # Example

                    # Dump all unique topic1 values for debugging
                    seen_topics = set()
                    for log in exec_receipt.get("logs", []):
                        topics = log.get("topics", [])
                        if len(topics) > 1:
                            topic1 = topics[1].hex() if isinstance(topics[1], bytes) else topics[1]
                            topic1_clean = topic1[2:] if topic1.startswith("0x") else topic1
                            if topic1_clean not in seen_topics:
                                seen_topics.add(topic1_clean)
                                if topic1_clean == ORDER_EXECUTED:
                                    console.print(f"  [green]✓ Found OrderExecuted event[/green]")
                                elif topic1_clean == ORDER_CANCELLED:
                                    console.print(f"  [red]✗ Found OrderCancelled event![/red]")
                                elif "cancelled" in topic1_clean.lower() or "cancel" in topic1_clean.lower():
                                    console.print(f"  [red]✗ Possible cancel event: {topic1_clean[:20]}...[/red]")
                    console.print(f"  Unique event types: {len(seen_topics)}")

                except Exception as e:
                    console.print(f"[red]✗ Keeper execution failed: {e}[/red]")
                    import traceback

                    traceback.print_exc()

            # ========================================================================
            # STEP 8: Verify Swap Results
            # ========================================================================
            console.print("\n[bold]Verifying swap results...[/bold]")
            time.sleep(2)  # Brief wait for state to settle

            final_usdc = usdc_token.contract.functions.balanceOf(wallet_address).call()
            final_weth = weth_token.contract.functions.balanceOf(wallet_address).call()
            final_eth = web3.eth.get_balance(wallet_address)

            # Also check vault balances to debug
            order_vault = to_checksum_address("0x31ef83a530fde1b38ee9a18093a333d8bbbc40d5")
            vault_usdc = usdc_token.contract.functions.balanceOf(order_vault).call()
            vault_weth = weth_token.contract.functions.balanceOf(order_vault).call()
            vault_eth = web3.eth.get_balance(order_vault)
            console.print(f"\n[bold]Vault Balances (debug):[/bold]")
            console.print(f"  Vault USDC: {vault_usdc / 10**6:.2f}")
            console.print(f"  Vault WETH: {vault_weth / 10**18:.6f}")
            console.print(f"  Vault ETH:  {vault_eth / 10**18:.6f}")

            # Check market pool (ETH/USD market)
            eth_market = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")
            market_usdc = usdc_token.contract.functions.balanceOf(eth_market).call()
            market_weth = weth_token.contract.functions.balanceOf(eth_market).call()
            console.print(f"\n[bold]ETH Market Pool Balances (debug):[/bold]")
            console.print(f"  Market USDC: {market_usdc / 10**6:.2f}")
            console.print(f"  Market WETH: {market_weth / 10**18:.6f}")

            console.print("\n[bold]Final Balances:[/bold]")
            console.print(f"  USDC: {final_usdc / 10**6:.2f}")
            console.print(f"  WETH: {final_weth / 10**18:.6f}")
            console.print(f"  ETH:  {final_eth / 10**18:.6f}")

            console.print("\n[bold]Balance Changes:[/bold]")
            usdc_change = (final_usdc - initial_usdc) / 10**6
            weth_change = (final_weth - initial_weth) / 10**18
            eth_change = (final_eth - initial_eth) / 10**18

            usdc_color = "green" if usdc_change > 0 else "red" if usdc_change < 0 else "white"
            weth_color = "green" if weth_change > 0 else "red" if weth_change < 0 else "white"
            eth_color = "green" if eth_change > 0 else "red" if eth_change < 0 else "white"

            console.print(f"  USDC: [{usdc_color}]{usdc_change:+.2f}[/{usdc_color}]")
            console.print(f"  WETH: [{weth_color}]{weth_change:+.6f}[/{weth_color}]")
            console.print(f"  ETH:  [{eth_color}]{eth_change:+.6f}[/{eth_color}] (includes gas)")

            # Verify swap was successful
            if args.direction == "usdc-to-eth":
                # For USDC->ETH, output could be WETH or native ETH (with shouldUnwrapNativeToken)
                # Gas is ~0.01-0.02 ETH, so we need to account for that when checking ETH gains
                eth_gain_net = eth_change + 0.02  # Add back estimated gas costs

                if usdc_change < 0 and weth_change > 0:
                    console.print(f"\n[green]✓ Swap successful! Received {weth_change:.6f} WETH for {-usdc_change:.2f} USDC[/green]")
                elif usdc_change < 0 and eth_gain_net > 0:
                    # Native ETH received (unwrapped)
                    console.print(f"\n[green]✓ Swap successful! Received ~{eth_gain_net:.6f} ETH for {-usdc_change:.2f} USDC[/green]")
                    console.print(f"  [dim](ETH change includes gas costs)[/dim]")
                elif usdc_change < 0:
                    console.print(f"\n[yellow]⚠ USDC was spent ({-usdc_change:.2f}) but no ETH/WETH received[/yellow]")
                else:
                    console.print(f"\n[red]✗ Swap failed - no balance changes detected[/red]")
            else:
                # ETH->USDC swap
                if (weth_change < 0 or eth_change < -0.02) and usdc_change > 0:
                    if weth_change < 0:
                        console.print(f"\n[green]✓ Swap successful! Received {usdc_change:.2f} USDC for {-weth_change:.6f} WETH[/green]")
                    else:
                        # Native ETH was spent (accounting for gas)
                        eth_spent = -eth_change - 0.015  # Subtract estimated gas
                        console.print(f"\n[green]✓ Swap successful! Received {usdc_change:.2f} USDC for ~{eth_spent:.6f} ETH[/green]")
                else:
                    console.print(f"\n[yellow]⚠ Swap may not have completed as expected[/yellow]")

        else:
            console.print(f"\n[red]✗ Swap order failed[/red]")
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

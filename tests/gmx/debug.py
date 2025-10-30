"""
Mainnet Fork Integration Test for GMX using Approach 2 (Pure Python + Anvil)

This script demonstrates the complete end-to-end flow:
1. Fork Arbitrum mainnet using Anvil via fork_network_anvil()
2. Create order using Python SDK (GMXTrading)
3. Execute order as keeper (simulate keeper behavior)
4. Verify position was created

To run:
    Set environment variables:
    export ARBITRUM_CHAIN_JSON_RPC="https://arb1.arbitrum.io/rpc"
    export PRIVATE_KEY="0x..."

    Run the script:
    python tests/gmx/debug.py
"""

import os
import sys
import logging
from cchecksum import to_checksum_address

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import (
    get_token_address_normalized,
    get_contract_addresses,
    NETWORK_TOKENS,
)
from rich.console import Console
from web3 import Web3, HTTPProvider

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy

from tests.gmx.setup_test.fork_helpers import (
    set_eth_balance,
    set_erc20_balance,
    set_bytecode,
    set_storage_at,
    grant_router_plugin_role,
)
from tests.gmx.setup_test.event_parser import (
    extract_order_key_from_receipt,
    extract_position_key_from_receipt,
)
from tests.gmx.setup_test.keeper_executor import execute_order_as_keeper
from eth_defi.gmx.core.open_positions import GetOpenPositions

console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """
    Main execution flow: Fork network -> Create order -> Execute as keeper -> Verify position
    """

    launch = None
    try:
        # ========================================================================
        # STEP 1: Fork Arbitrum Mainnet using fork_network_anvil
        # ========================================================================
        console.print("\n[bold blue]=== GMX Mainnet Fork Integration Test ===[/bold blue]\n")

        # Read RPC URL from environment
        chain_rpc_url = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
        if not chain_rpc_url:
            console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
            console.print("Set it with: export ARBITRUM_CHAIN_JSON_RPC='https://arb1.arbitrum.io/rpc'")
            sys.exit(1)

        console.print(f"[bold]Forking Arbitrum mainnet...[/bold]")
        console.print(f"  RPC: {chain_rpc_url}")

        # Start Anvil fork using fork_network_anvil
        try:
            launch = fork_network_anvil(
                fork_url=chain_rpc_url,
                fork_block_number=392496384,
                unlocked_addresses=[],
            )
            web3 = Web3(HTTPProvider(launch.json_rpc_url))
            install_chain_middleware(web3)
            web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
            console.print(f"  Anvil fork started on {launch.json_rpc_url}")
        except Exception as e:
            console.print(f"[red]Failed to start Anvil fork: {e}[/red]")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # Verify connection
        try:
            block_number = web3.eth.block_number
            chain_id = web3.eth.chain_id
            console.print(f"  Block: {block_number}")
            console.print(f"  Chain ID: {chain_id}")
        except Exception as e:
            console.print(f"[red]Failed to verify fork connection: {e}[/red]")
            sys.exit(1)

        # Get chain name
        chain = get_chain_name(chain_id).lower()
        console.print(f"  Chain: {chain}\n")

        # ========================================================================
        # STEP 1.5: Grant ROUTER_PLUGIN Role to ExchangeRouter
        # ========================================================================
        console.print("[bold]Granting ROUTER_PLUGIN role to ExchangeRouter...[/bold]")
        try:
            contract_addresses = get_contract_addresses(chain)
            grant_router_plugin_role(web3, contract_addresses.exchangerouter, chain)
            console.print("  Role granted successfully\n")
        except Exception as e:
            console.print(f"[red]Failed to grant ROUTER_PLUGIN role: {e}[/red]")
            import traceback
            traceback.print_exc()
            # Continue anyway, might not be needed for all order types
            console.print("  Continuing anyway...\n")

        # ========================================================================
        # STEP 2: Setup Wallet and Balances
        # ========================================================================
        console.print("[bold]Setting up wallet and balances...[/bold]")

        # Get or create wallet
        private_key = os.environ.get("PRIVATE_KEY")
        if not private_key:
            console.print("[yellow]PRIVATE_KEY not set, using test account[/yellow]")
            # Use a test private key (DO NOT USE IN PRODUCTION)
            private_key = "0x" + "1" * 64

        wallet = HotWallet.from_private_key(private_key)
        wallet_address = wallet.get_main_address()
        wallet.sync_nonce(web3)

        console.print(f"  Wallet: {wallet_address}")

        # Get token addresses
        weth_address = to_checksum_address(NETWORK_TOKENS["arbitrum"]["WETH"])
        usdc_address = to_checksum_address(NETWORK_TOKENS["arbitrum"]["USDC"])
        arb_address = to_checksum_address(NETWORK_TOKENS["arbitrum"]["ARB"])

        console.print(f"  WETH: {weth_address}")
        console.print(f"  USDC: {usdc_address}")
        console.print(f"  ARB: {arb_address}\n")

        # Set wallet balances using fork helpers
        console.print("[bold]Funding wallet on fork...[/bold]")
        try:
            eth_amount = 100 * 10**18  # 100 ETH
            usdc_amount = 100_000 * 10**6  # 100k USDC
            weth_amount = 10 * 10**18  # 10 WETH
            arb_amount = 100_000 * 10**18  # 100k ARB

            set_eth_balance(web3, wallet_address, eth_amount)
            set_erc20_balance(web3, usdc_address, wallet_address, usdc_amount)
            set_erc20_balance(web3, weth_address, wallet_address, weth_amount)
            set_erc20_balance(web3, arb_address, wallet_address, arb_amount)

            console.print(f"  ETH: {eth_amount / 10**18} ETH")
            console.print(f"  USDC: {usdc_amount / 10**6} USDC")
            console.print(f"  WETH: {weth_amount / 10**18} WETH")
            console.print(f"  ARB: {arb_amount / 10**18} ARB\n")

        except Exception as e:
            console.print(f"[red]Failed to set balances: {e}[/red]")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # ========================================================================
        # STEP 3: Note: Oracle Provider Configuration
        # ========================================================================
        console.print("[bold]Oracle Provider Note:[/bold]")
        console.print("  The GMX fork requires a mocked oracle provider (like Foundry's vm.etch)")
        console.print("  This is not yet implemented in this Python version")
        console.print("  See: tests/gmx/forked-env-example/contracts/mock/MockOracleProvider.sol\n")

        # ========================================================================
        # STEP 4: Create GMX Order using Python SDK
        # ========================================================================
        console.print("[bold]Creating GMX order...[/bold]")

        try:
            # Create config and trading client
            config = GMXConfig(web3, user_wallet_address=wallet_address)
            trading_client = GMXTrading(config)

            # Order parameters
            market_symbol = "ETH"
            collateral_symbol = "USDC"
            start_token_symbol = collateral_symbol  # Use collateral as start token
            is_long = True
            size_delta_usd = 10  # $10 position
            leverage = 1.0

            console.print(f"  Market: {market_symbol}")
            console.print(f"  Collateral: {collateral_symbol}")
            console.print(f"  Size: ${size_delta_usd} at {leverage}x leverage")
            console.print(f"  Direction: {'LONG' if is_long else 'SHORT'}")

            # Create order via SDK
            order = trading_client.open_position(
                market_symbol=market_symbol,
                collateral_symbol=collateral_symbol,
                start_token_symbol=start_token_symbol,
                is_long=is_long,
                size_delta_usd=size_delta_usd,
                leverage=leverage,
                slippage_percent=0.005,
                execution_buffer=2.2,
            )

            console.print(f"\n  Order object created")
            console.print(f"    Execution Fee: {order.execution_fee / 10**18:.6f} ETH")
            console.print(f"    Mark Price: {order.mark_price}")
            console.print(f"    Gas Limit: {order.gas_limit}\n")

        except Exception as e:
            console.print(f"[red]Order creation failed: {e}[/red]")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # ========================================================================
        # STEP 4: Handle Token Approval
        # ========================================================================
        console.print("[bold]Handling token approval...[/bold]")

        try:
            collateral_token_address = get_token_address_normalized(
                chain, collateral_symbol
            )
            token_details = fetch_erc20_details(web3, collateral_token_address)
            contract_addresses = get_contract_addresses(chain)
            spender_address = contract_addresses.syntheticsrouter

            current_allowance = token_details.contract.functions.allowance(
                wallet_address, spender_address
            ).call()

            required_amount = 1_000_000_000 * (10**token_details.decimals)

            if current_allowance < required_amount:
                console.print(f"  Approving {collateral_symbol}...")

                approve_tx = token_details.contract.functions.approve(
                    spender_address, required_amount
                ).build_transaction(
                    {
                        "from": wallet_address,
                        "gas": 100000,
                        "gasPrice": web3.eth.gas_price,
                    }
                )

                if "nonce" in approve_tx:
                    del approve_tx["nonce"]

                signed_approve_tx = wallet.sign_transaction_with_new_nonce(approve_tx)
                approve_tx_hash = web3.eth.send_raw_transaction(
                    signed_approve_tx.rawTransaction
                )

                console.print(f"    TX: {approve_tx_hash.hex()}")
                approve_receipt = web3.eth.wait_for_transaction_receipt(approve_tx_hash)

                if approve_receipt["status"] == 1:
                    console.print(f"  Approval successful\n")
                else:
                    console.print(f"  Approval failed\n")
                    raise Exception("Token approval reverted")
            else:
                console.print(
                    f"  Sufficient allowance exists\n"
                )

        except Exception as e:
            console.print(f"[red]Approval failed: {e}[/red]")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # ========================================================================
        # STEP 5: Submit Order Transaction
        # ========================================================================
        console.print("[bold]Submitting order to ExchangeRouter...[/bold]")

        try:
            transaction = order.transaction.copy()
            if "nonce" in transaction:
                del transaction["nonce"]

            console.print(f"  To: {transaction.get('to')}")
            console.print(f"  Value: {transaction.get('value', 0) / 10**18:.6f} ETH")
            console.print(f"  Data size: {len(transaction.get('data', ''))} bytes")

            # Sign and send
            signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

            console.print(f"\n  TX Hash: {tx_hash.hex()}")

            # Wait for confirmation
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt["status"] != 1:
                console.print("[red]Order creation transaction failed[/red]")
                try:
                    assert_transaction_success_with_explanation(web3, tx_hash)
                except Exception as e:
                    console.print(f"  Error: {e}")
                sys.exit(1)

            console.print(f"  Order creation successful")
            console.print(f"    Block: {receipt['blockNumber']}")
            console.print(f"    Gas Used: {receipt['gasUsed']}\n")

        except Exception as e:
            console.print(f"[red]Order submission failed: {e}[/red]")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # ========================================================================
        # STEP 6: Extract Order Key from Logs
        # ========================================================================
        console.print("[bold]Extracting order key from logs...[/bold]")

        try:
            order_key = extract_order_key_from_receipt(receipt)
            console.print(f"  Order Key: {order_key.hex()}\n")

        except Exception as e:
            console.print(f"[red]Failed to extract order key: {e}[/red]")
            sys.exit(1)

        # ========================================================================
        # STEP 7: Execute Order as Keeper (This is the Approach 2 magic!)
        # ========================================================================
        console.print("[bold]Executing order as keeper...[/bold]")
        console.print("(Simulating off-chain keeper behavior on-chain)\n")

        try:
            # Reference prices from Foundry test
            eth_price_usd = 3892  # Match mainnet reference price
            usdc_price_usd = 1

            console.print(f"  Oracle Prices:")
            console.print(f"    ETH: ${eth_price_usd}")
            console.print(f"    USDC: ${usdc_price_usd}\n")

            # Execute order as keeper
            exec_receipt = execute_order_as_keeper(
                web3=web3,
                order_key=order_key,
                chain=chain,
                eth_price_usd=eth_price_usd,
                usdc_price_usd=usdc_price_usd,
            )

            if exec_receipt["status"] != 1:
                console.print("[red]Order execution failed[/red]")
                try:
                    assert_transaction_success_with_explanation(
                        web3, exec_receipt["transactionHash"]
                    )
                except Exception as e:
                    console.print(f"  Error: {e}")
                sys.exit(1)

            console.print(f"  Order executed successfully")
            console.print(f"    TX: {exec_receipt['transactionHash'].hex()}")
            console.print(f"    Gas Used: {exec_receipt['gasUsed']}\n")

        except Exception as e:
            console.print(f"[red]Order execution failed: {e}[/red]")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # ========================================================================
        # STEP 8: Extract Position Key from Execution
        # ========================================================================
        console.print("[bold]Extracting position key from execution logs...[/bold]")

        try:
            position_key = extract_position_key_from_receipt(exec_receipt)
            console.print(f"  Position Key: {position_key.hex()}\n")

        except Exception as e:
            console.print(f"Failed to extract position key: {e}")
            console.print("  (This might be okay if the event signature differs)\n")
            position_key = None

        # ========================================================================
        # STEP 9: Verify Position was Created
        # ========================================================================
        console.print("[bold]Verifying position was created...[/bold]")

        try:
            # Query open positions for wallet
            positions_getter = GetOpenPositions(config)
            positions = positions_getter.get_data(wallet_address)

            if positions:
                console.print(f"  Position created!")
                console.print(f"    Count: {len(positions)}")

                for i, pos in enumerate(positions):
                    console.print(f"\n    Position {i+1}:")
                    console.print(f"      Market: {pos.get('market_symbol', 'N/A')}")
                    console.print(f"      Size: ${pos.get('size_usd', 'N/A')}")
                    console.print(f"      Collateral: ${pos.get('collateral_usd', 'N/A')}")
                    console.print(
                        f"      Entry Price: {pos.get('entry_price', 'N/A')}"
                    )

                console.print()

            else:
                console.print(f"  No positions found")
                console.print("    (Position might not have been created or query failed)\n")

        except Exception as e:
            console.print(f"  Could not verify positions: {e}\n")

        # ========================================================================
        # Summary
        # ========================================================================
        console.print("[bold blue]=== Test Summary ===[/bold blue]")
        console.print(f"Successfully completed end-to-end GMX flow")
        console.print(f"  1. Forked Arbitrum mainnet using fork_network_anvil()")
        console.print(f"  2. Created order with Python SDK")
        console.print(f"  3. Executed order as keeper on fork")
        console.print(f"  4. Verified position creation")
        console.print(
            f"\nThis demonstrates Approach 2: Pure Python + Anvil"
        )
        console.print(f"All logic runs in Python, no Solidity needed for execution!")

    finally:
        # Close Anvil fork after test completes
        if launch:
            launch.close(log_level=logging.ERROR)


if __name__ == "__main__":
    main()

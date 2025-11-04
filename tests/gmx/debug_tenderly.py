"""
GMX Order Creation Test - Tenderly Fork Only

This script demonstrates GMX order creation on Tenderly virtual testnet.

Usage:
    export TD_ARB="https://virtual.arbitrum.rpc.tenderly.co/YOUR_FORK_ID"
    export PRIVATE_KEY="0x..."
    python tests/gmx/debug_tenderly.py
"""

import os
import sys
import logging
from rich.console import Console
from rich.logging import RichHandler
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import get_token_address_normalized, get_contract_addresses
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import execute_order_as_keeper, setup_mock_oracle, extract_order_key_from_receipt

# Configure logging
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
logger = logging.getLogger("rich")

console = Console()


def tenderly_set_balance(web3: Web3, wallet_address: str, amount_eth: float):
    """Set ETH balance on Tenderly fork."""
    amount_wei = int(amount_eth * 1e18)
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(amount_wei)])
    console.print(f"  [green]Set ETH balance: {amount_eth} ETH[/green]")


def tenderly_set_erc20_balance(web3: Web3, token_address: str, wallet_address: str, amount: int):
    """Set ERC20 token balance on Tenderly fork."""
    web3.provider.make_request("tenderly_setErc20Balance", [token_address, wallet_address, hex(amount)])
    token_details = fetch_erc20_details(web3, token_address)
    formatted_amount = amount / (10**token_details.decimals)
    console.print(f"  [green]Set {token_details.symbol} balance: {formatted_amount:.2f}[/green]")


def main():
    """Main execution flow for Tenderly fork."""

    # Get required environment variables
    tenderly_rpc = os.environ.get("TD_ARB")
    if not tenderly_rpc:
        console.print("[red]Error: TD_ARB environment variable not set[/red]")
        console.print("[yellow]Set TD_ARB to your Tenderly fork RPC URL:[/yellow]")
        console.print("[yellow]  export TD_ARB='https://virtual.arbitrum.rpc.tenderly.co/YOUR_FORK_ID'[/yellow]")
        sys.exit(1)

    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    try:
        console.print("\n[bold green]=== GMX Tenderly Fork Test ===[/bold green]\n")

        # ========================================================================
        # STEP 1: Connect to Tenderly Fork
        # ========================================================================
        console.print("Connecting to Tenderly fork...")
        web3 = create_multi_provider_web3(tenderly_rpc)

        block_number = web3.eth.block_number
        chain_id = web3.eth.chain_id
        chain = get_chain_name(chain_id).lower()

        console.print(f"  Block: {block_number}")
        console.print(f"  Chain ID: {chain_id}")
        console.print(f"  Chain: {chain}\n")

        # ========================================================================
        # STEP 2: Setup Mock Oracle
        # ========================================================================
        # TODO: Even after setting this the code is not bypassing the checks
        console.print("[bold]Setting up mock oracle provider...[/bold]")
        setup_mock_oracle(web3, eth_price_usd=3892, usdc_price_usd=1)
        console.print("[green]✓ Mock oracle configured[/green]\n")

        # ========================================================================
        # STEP 3: Setup Wallet
        # ========================================================================
        console.print("[bold]Setting up wallet...[/bold]")
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
        # STEP 4: Fund Wallet
        # ========================================================================
        console.print("\n[bold]Funding wallet...[/bold]")

        # Fund with ETH
        tenderly_set_balance(web3, wallet_address, 100.0)

        # Fund with USDC
        usdc_address = tokens.get("USDC")
        if usdc_address:
            usdc_amount = 100_000 * (10**6)  # 100k USDC
            tenderly_set_erc20_balance(web3, usdc_address, wallet_address, usdc_amount)

        # Fund with WETH
        weth_address = tokens.get("WETH")
        if weth_address:
            weth_amount = 1000 * (10**18)  # 1000 WETH
            tenderly_set_erc20_balance(web3, weth_address, wallet_address, weth_amount)

        # ========================================================================
        # STEP 5: Create GMX Order
        # ========================================================================
        console.print("\n[bold]Creating GMX order...[/bold]")

        # Configure position
        market_symbol = "ETH"
        collateral_symbol = "ETH"  # ETH gets auto-wrapped to WETH by GMX
        start_token_symbol = "ETH"
        leverage = 2.5
        size_usd = 10.0

        console.print(f"  Market: {market_symbol}")
        console.print(f"  Collateral: {collateral_symbol}")
        console.print(f"  Size: ${size_usd} at {leverage}x leverage")
        console.print(f"  Direction: LONG")

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
        # STEP 6: Handle Token Approval (if needed)
        # ========================================================================
        console.print("\n[bold]Handling token approval...[/bold]")

        # ETH doesn't need approval (native currency)
        if collateral_symbol in ["ETH", "WETH"]:
            console.print(f"  [green]Using native ETH - no approval needed[/green]")
        else:
            # Handle ERC20 approval
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
        # STEP 7: Submit Order
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

            # ====================================================================
            # STEP 8: Extract Order Key
            # ====================================================================
            order_key = None
            try:
                order_key = extract_order_key_from_receipt(receipt)
                console.print(f"\n[green]✓ Order Key: {order_key.hex()}[/green]")
            except Exception as e:
                console.print(f"\n[yellow]Warning: Could not extract order key: {e}[/yellow]")

            # ====================================================================
            # STEP 9: Execute Order as Keeper
            # ====================================================================
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

            # ====================================================================
            # STEP 10: Verify Position
            # ====================================================================
            console.print("\n[bold]Verifying position...[/bold]")

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

            try:
                assert_transaction_success_with_explanation(web3, tx_hash)
            except Exception as e:
                console.print(f"  Error: {str(e)}")

    except Exception as e:
        console.print(f"\n[red]Error: {str(e)}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

"""GMX CCXT Bundled Stop-Loss and Take-Profit Example - Arbitrum Sepolia Testnet.

This script demonstrates creating a position with automatic stop-loss and take-profit
orders on GMX V2 testnet using CCXT-style parameters.

Prerequisites:
- Arbitrum Sepolia testnet ETH for gas
- Testnet USDC for collateral (get from GMX faucet)
- Private key with funded wallet

Usage:
    export PRIVATE_KEY=your_private_key_here
    export ARBITRUM_SEPOLIA_RPC_URL=https://sepolia-rollup.arbitrum.io/rpc
    python scripts/gmx/gmx_ccxt_stop_loss_bundled_testnet.py

GMX V2 Testnet Info:
- Network: Arbitrum Sepolia
- Chain ID: 421614
"""

import logging
import os
import sys
from rich.console import Console
from rich.logging import RichHandler

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_token_address_normalized
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

# Configure logging to show approval messages
logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler(console=Console(), show_time=False, show_path=False)])

console = Console()


def main():
    """Run bundled SL/TP example on testnet."""
    console.print("\n" + "=" * 80)
    console.print("GMX CCXT Bundled SL/TP Example - Arbitrum Sepolia Testnet")
    console.print("=" * 80)

    # Get configuration from environment
    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        console.print("Set it with: export PRIVATE_KEY=0x...")
        return 1

    # Use same environment variable as gmx_open_position.py
    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc")

    console.print(f"Starting GMX CCXT Bundled SL/TP Test...")
    console.print(f"Connecting to: {rpc_url}")

    try:
        # Connect to testnet
        web3 = create_multi_provider_web3(rpc_url)

        # Verify connection
        try:
            block_number = web3.eth.block_number
            console.print(f"Connected to network, current block: [blue]{block_number}[/blue]")
        except Exception as e:
            console.print(f"[red]Failed to connect to RPC: {e}[/red]")
            sys.exit(1)

        # Get chain from web3 object and normalize
        chain_raw = get_chain_name(web3.eth.chain_id)
        console.print(f"Detected chain raw: {chain_raw}")

        # Normalize chain name for GMX API
        # Map chain IDs to GMX API expected names (with underscores)
        chain_id_to_gmx_chain = {
            42161: "arbitrum",
            421614: "arbitrum_sepolia",
            43114: "avalanche",
            43113: "avalanche_fuji",
        }

        chain_id = web3.eth.chain_id
        if chain_id in chain_id_to_gmx_chain:
            chain = chain_id_to_gmx_chain[chain_id]
        else:
            # Fallback to lowercased chain name
            chain = chain_raw.lower()

        console.print(f"Normalized chain for GMX: [blue]{chain}[/blue]")

        if chain_id != 421614:
            console.print(f"[yellow]Warning: Expected Arbitrum Sepolia (421614), got {chain_id}[/yellow]")

        # Create wallet
        wallet = HotWallet.from_private_key(private_key)
        wallet.sync_nonce(web3)
        console.print(f"Wallet address: [yellow]{wallet.address}[/yellow]")

        # Check balances
        eth_balance = web3.eth.get_balance(wallet.address)
        console.print(f"ETH balance: {eth_balance / 10**18:.6f} ETH")

        usdc_address = get_token_address_normalized(chain, "USDC")
        usdc_token = fetch_erc20_details(web3, usdc_address)
        usdc_balance = usdc_token.contract.functions.balanceOf(wallet.address).call()
        console.print(f"USDC balance: {usdc_balance / 10**6:.2f} USDC")

        if eth_balance == 0:
            console.print("\n[yellow]Warning: No ETH for gas. Get testnet ETH from Arbitrum Sepolia faucet[/yellow]")
        if usdc_balance < 10 * 10**6:  # Less than $10 USDC
            console.print("\n[yellow]Warning: Low USDC balance. Get testnet tokens.[/yellow]")

        # Initialize GMX CCXT
        # Note: Token approvals are handled automatically by the GMX CCXT wrapper
        console.print("\nInitializing GMX CCXT...")
        config = GMXConfig(web3=web3, user_wallet_address=wallet.address)
        gmx = GMX(config, wallet=wallet)
        gmx.load_markets()
        console.print(f"Loaded {len(gmx.markets)} markets")

        # Fetch current ETH price
        ticker = gmx.fetch_ticker("ETH/USDC:USDC")
        current_price = ticker["last"]
        console.print(f"\nCurrent ETH price: ${current_price:,.2f}")

        # Calculate SL/TP prices (5% stop loss, 10% take profit)
        stop_loss_price = current_price * 0.95
        take_profit_price = current_price * 1.10
        console.print(f"Stop Loss: ${stop_loss_price:,.2f} (-5%)")
        console.print(f"Take Profit: ${take_profit_price:,.2f} (+10%)")

        # Example 1: CCXT unified style
        console.print("\n" + "=" * 80)
        console.print("Creating position with bundled SL/TP (CCXT unified style)...")
        console.print("=" * 80)
        console.print("\n[dim]Note: Token approvals will be checked and executed automatically if needed[/dim]\n")

        try:
            order = gmx.create_market_buy_order(
                "ETH/USDC:USDC",
                10.0,  # $10 USD position size
                {
                    "leverage": 1.5,  # 1.5x leverage
                    "collateral_symbol": "ETH",  # Use ETH as collateral
                    "slippage_percent": 0.005,  # 0.5% slippage
                    "execution_buffer": 5,  # Execution fee buffer (minimum 5)
                    # CCXT unified style SL/TP
                    "stopLossPrice": stop_loss_price,
                    "takeProfitPrice": take_profit_price,
                },
            )

            console.print("\n[green]Position created successfully![/green]")
            console.print(f"Transaction hash: [yellow]{order['id']}[/yellow]")

            # Debug: Print execution fees
            if "info" in order:
                console.print(f"\nExecution Fees:")
                console.print(f"  Total: {order['info']['total_execution_fee'] / 10**18:.6f} ETH")
                console.print(f"  Main order: {order['info']['main_order_fee'] / 10**18:.6f} ETH")
                console.print(f"  Stop loss: {order['info']['stop_loss_fee'] / 10**18:.6f} ETH")
                console.print(f"  Take profit: {order['info']['take_profit_fee'] / 10**18:.6f} ETH")

            # Verify transaction success
            try:
                assert_transaction_success_with_explanation(web3, order["id"])
                console.print("[green]Transaction confirmed successfully![/green]")
            except Exception as trace_error:
                console.print(f"[red]Transaction may have failed: {trace_error}[/red]")

            console.print(f"Status: {order['status']}")
            console.print(f"Symbol: {order['symbol']}")
            console.print(f"Side: {order['side']}")
            console.print(f"Amount: ${order['amount']}")

            if order["info"].get("has_stop_loss"):
                console.print(f"\n[cyan]Stop Loss:[/cyan]")
                console.print(f"  Trigger: ${order['info']['stop_loss_trigger']:,.2f}")

            if order["info"].get("has_take_profit"):
                console.print(f"\n[cyan]Take Profit:[/cyan]")
                console.print(f"  Trigger: ${order['info']['take_profit_trigger']:,.2f}")

            console.print("\n[green]GMX CCXT Bundled SL/TP Test completed successfully![/green]")
            return 0

        except Exception as e:
            console.print(f"\n[red]Error creating position: {e}[/red]")
            console.print("\nTo successfully execute transactions, ensure:")
            console.print("  - Sufficient USDC balance in wallet")
            console.print("  - Token approval for GMX contracts")
            console.print("  - Sufficient ETH for gas fees")
            import traceback

            traceback.print_exc()
            return 1

    except Exception as e:
        console.print(f"\n[red]Error during execution: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    exit(main())

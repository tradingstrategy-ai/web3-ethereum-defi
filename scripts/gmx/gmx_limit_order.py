"""
GMX Limit Order Test Script

This script demonstrates how to create limit orders on GMX protocol using the
eth_defi framework. Limit orders execute when the market price reaches your
specified trigger price.

The script performs the following operations:

1. Connects to Arbitrum Sepolia testnet via RPC
2. Detects the chain and loads GMX contract addresses
3. Creates a trading client with the provided wallet
4. Creates a limit order that executes when price reaches the trigger
5. Handles token approvals automatically
6. Signs and submits the transaction

Limit Order Behaviour
---------------------

**Long Limit Order:**
- Set trigger price BELOW current market price
- Order executes when price drops to trigger (buy the dip)
- Example: Market at $2000, trigger at $1900

**Short Limit Order:**
- Set trigger price ABOVE current market price
- Order executes when price rises to trigger (sell the rally)
- Example: Market at $2000, trigger at $2100

Usage
-----

Basic usage with environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_limit_order.py

For Arbitrum mainnet::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_RPC_URL="https://arbitrum-mainnet.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_limit_order.py

Environment Variables
---------------------

- ``PRIVATE_KEY``: Your wallet's private key (required)
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint
- ``ARBITRUM_RPC_URL``: Arbitrum mainnet RPC endpoint

Example
-------

Create a $10 limit long order on ETH market::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_limit_order.py

Notes
-----

- This example uses a fixed trigger price for simplicity
- In production, you should fetch current market price and calculate trigger price dynamically
- Limit orders remain pending until market price reaches the trigger price
- Keepers execute the order once conditions are met
- Order expires if not executed within the specified time window
- Ensure sufficient collateral tokens (USDC) and ETH for execution fees

See Also
--------

- :meth:`eth_defi.gmx.trading.GMXTrading.open_limit_position` - Limit order API
- :mod:`eth_defi.gmx.order.increase_order` - Limit order implementation
- :mod:`eth_defi.gmx.trading` - GMX trading module
"""

import logging
import os
import sys

from rich.logging import RichHandler

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_token_address_normalized
from eth_defi.gmx.gas_monitor import GasMonitorConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from rich.console import Console

# Configure logging to show gas monitoring and trading logs
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

# Enable logging for eth_defi modules (gas monitoring, trading, etc.)
logging.getLogger("eth_defi").setLevel(logging.INFO)
logging.getLogger("eth_defi.gmx.trading").setLevel(logging.INFO)
logging.getLogger("eth_defi.gmx.gas_monitor").setLevel(logging.INFO)

console = Console()


def main():
    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    private_key = os.environ.get("PRIVATE_KEY")

    if not rpc_url:
        console.print("[red]Error: ARBITRUM_SEPOLIA_RPC_URL environment variable not set[/red]")
        sys.exit(1)

    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    console.print("\n[bold green]=== GMX Limit Order Test - Arbitrum Sepolia ===[/bold green]\n")

    # Create web3 provider
    web3 = create_multi_provider_web3(rpc_url)

    # Verify connection
    try:
        block_number = web3.eth.block_number
        chain_id = web3.eth.chain_id
        chain = get_chain_name(chain_id).lower()
        console.print("Connected to network")
        console.print(f"  Block: {block_number}")
        console.print(f"  Chain: {chain} (ID: {chain_id})")
    except Exception as e:
        console.print(f"[red]Failed to connect to RPC: {e}[/red]")
        sys.exit(1)

    # Create wallet
    wallet = HotWallet.from_private_key(private_key)
    wallet_address = wallet.get_main_address()
    wallet.sync_nonce(web3)

    console.print(f"\n[bold]Wallet Setup:[/bold]")
    console.print(f"  Address: {wallet_address}")

    # Check balances
    eth_balance = web3.eth.get_balance(wallet_address)
    console.print(f"  ETH Balance: {eth_balance / 10**18:.6f} ETH")

    # Create GMX config with gas monitoring
    config = GMXConfig(web3, user_wallet_address=wallet_address)
    gas_config = GasMonitorConfig(enabled=True)
    trading_client = GMXTrading(config, gas_monitor_config=gas_config)

    # Market configuration
    user_market_symbol = "ETH"
    user_collateral_symbol = "USDC.SG"
    user_start_token_symbol = "USDC.SG"

    # Token symbol mapping for markets with different index token names
    symbol_alias_mapping = {
        "WBTC": "BTC",
    }

    market_symbol = symbol_alias_mapping.get(user_market_symbol.upper(), user_market_symbol.upper())
    collateral_symbol = symbol_alias_mapping.get(user_collateral_symbol.upper(), user_collateral_symbol.upper())
    start_token_symbol = symbol_alias_mapping.get(user_start_token_symbol.upper(), user_start_token_symbol.upper())

    # Order parameters
    size_usd = 10  # Position size in USD
    leverage = 2.0  # Leverage multiplier

    # Set trigger price manually (for example script simplicity)
    # In production, you would fetch current price and calculate trigger price
    trigger_price_usd = 3000.0  # Example: trigger at $3000 for ETH

    console.print(f"\n[bold]Token Configuration:[/bold]")
    console.print(f"  Market Symbol: {market_symbol}")
    console.print(f"  Collateral Symbol: {collateral_symbol}")
    console.print(f"  Start Token Symbol: {start_token_symbol}")
    console.print(f"  Trigger Price: ${trigger_price_usd:,.2f}")
    console.print(f"\n[dim]This is a fixed trigger price for example purposes.[/dim]")
    console.print(f"[dim]In production, fetch current price and calculate trigger accordingly.[/dim]")

    try:
        # Create limit order
        console.print(f"\n[bold cyan]Creating Limit Order:[/bold cyan]")
        console.print(f"  Market: {user_market_symbol} ({market_symbol})")
        console.print(f"  Collateral: {collateral_symbol}")
        console.print(f"  Side: Long")
        console.print(f"  Size: ${size_usd}")
        console.print(f"  Leverage: {leverage}x")
        console.print(f"  Trigger Price: ${trigger_price_usd:,.2f}")
        console.print(f"  Order Type: LIMIT")

        order = trading_client.open_limit_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=start_token_symbol,
            is_long=True,
            size_delta_usd=size_usd,
            leverage=leverage,
            trigger_price=trigger_price_usd,
            slippage_percent=0.005,  # 0.5% slippage
            execution_buffer=30,
        )

        console.print(f"\n[green]Limit Order object created successfully![/green]")

        # Handle token approval
        try:
            collateral_token_address = get_token_address_normalized(chain, collateral_symbol)
            token_details = fetch_erc20_details(web3, collateral_token_address)
            token_contract = token_details.contract

            contract_addresses = get_contract_addresses(chain)
            spender_address = contract_addresses.syntheticsrouter

            console.print(f"\n[bold]Token Approval:[/bold]")
            console.print(f"  Token: {collateral_token_address}")
            console.print(f"  Spender: {spender_address}")

            current_allowance = token_contract.functions.allowance(wallet_address, spender_address).call()
            token_decimals = token_details.decimals
            required_amount = 1_000_000_000 * (10**token_decimals)

            console.print(f"  Current Allowance: {current_allowance / (10**token_decimals):.2f} {collateral_symbol}")

            if current_allowance < required_amount:
                console.print(f"  Approving {collateral_symbol} tokens...")

                approve_tx = token_contract.functions.approve(spender_address, required_amount).build_transaction(
                    {
                        "from": wallet_address,
                        "gas": 100000,
                        "gasPrice": web3.eth.gas_price,
                    }
                )

                if "nonce" in approve_tx:
                    del approve_tx["nonce"]

                try:
                    signed_approve_tx = wallet.sign_transaction_with_new_nonce(approve_tx)
                    approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)
                    console.print(f"  Approval TX: {approve_tx_hash.hex()}")

                    console.print("  Waiting for confirmation...")
                    approve_receipt = web3.eth.wait_for_transaction_receipt(approve_tx_hash)
                    console.print(f"  Approved! Block: {approve_receipt['blockNumber']}")
                except Exception as approval_error:
                    console.print(f"[red]Approval failed: {approval_error}[/red]")
            else:
                console.print(f"  [green]Sufficient allowance already exists[/green]")

        except Exception as e:
            console.print(f"[red]Token approval error: {e}[/red]")
            import traceback

            traceback.print_exc()

        # Sign and send limit order transaction
        try:
            console.print(f"\n[bold]Submitting Limit Order Transaction...[/bold]")

            transaction = order.transaction
            if "nonce" in transaction:
                del transaction["nonce"]

            signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

            console.print(f"  Transaction Hash: [yellow]{tx_hash.hex()}[/yellow]")

            assert_transaction_success_with_explanation(web3, tx_hash)

            console.print("  Waiting for confirmation...")
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
            console.print(f"  Confirmed! Block: {receipt['blockNumber']}")
            console.print(f"  Status: {receipt['status']}")

            # Display summary
            console.print("\n" + "=" * 70)
            console.print("[bold green]Limit Order Created Successfully![/bold green]")
            console.print("=" * 70)
            console.print(f"\n  Market: {market_symbol}")
            console.print(f"  Size: ${size_usd}")
            console.print(f"  Leverage: {leverage}x")
            console.print(f"  Trigger Price: ${trigger_price_usd:,.2f}")
            console.print(f"\n  Transaction: {tx_hash.hex()}")

            console.print("\n[bold]Next Steps:[/bold]")
            console.print("  1. Order is now pending on GMX")
            console.print(f"  2. When {market_symbol} price drops to ${trigger_price_usd:,.2f}, keepers will execute it")
            console.print("  3. Position will open with your specified size and leverage")
            console.print(f"\n[dim]Check order status on GMX interface using TX hash[/dim]")

        except Exception as e:
            console.print(f"[red]Transaction failed: {e}[/red]")
            import traceback

            traceback.print_exc()
            raise

        console.print("\n[green]GMX Limit Order Test completed successfully![/green]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

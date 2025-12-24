"""
GMX Position Closing Script

This script demonstrates how to close leveraged positions on GMX protocol
using the eth_defi framework. It supports both mainnet and testnet deployments.

The script performs the following operations:

1. Connects to the specified blockchain network via RPC
2. Detects the chain from the RPC URL
3. Loads GMX contract addresses and token information
4. Creates a trading client with the provided wallet
5. Closes an existing leveraged position on a specified market
6. Signs and submits the transaction

Usage
-----

Basic usage with environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_close_position.py

For Arbitrum mainnet::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_RPC_URL="https://arbitrum-mainnet.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_close_position.py

For Avalanche mainnet::

    export PRIVATE_KEY="0x1234..."
    export AVALANCHE_RPC_URL="https://avalanche-mainnet.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_close_position.py

Environment Variables
---------------------

The script requires the following environment variables:

- ``PRIVATE_KEY``: Your wallet's private key (required)
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint
- ``ARBITRUM_RPC_URL``: Arbitrum mainnet RPC endpoint
- ``AVALANCHE_RPC_URL``: Avalanche C-Chain mainnet RPC endpoint

Example
-------

Close a $10 USD position on CRV market on Arbitrum Sepolia::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_close_position.py

Notes
-----

- The script automatically detects the chain from the RPC URL
- Position parameters (market, size, type) can be modified in the script
- Ensure your wallet has sufficient ETH for gas fees
- You must have an existing position to close

See Also
--------

- :mod:`eth_defi.gmx.trading` - GMX trading module
- :mod:`eth_defi.gmx.config` - GMX configuration
- :class:`eth_defi.hotwallet.HotWallet` - Hot wallet implementation

"""

import os
import sys

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import get_tokens_address_dict, get_contract_addresses
from rich.console import Console
from rich.table import Table

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.trace import assert_transaction_success_with_explanation

console = Console()


def main():
    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    private_key = os.environ.get("PRIVATE_KEY")

    console.print("Starting GMX Position Closing Script...")

    # create a web3 provider
    web3 = create_multi_provider_web3(rpc_url)

    # Verify connection
    try:
        block_number = web3.eth.block_number
        console.print(f"Connected to network, current block: {block_number}")
    except Exception as e:
        console.print(f"Failed to connect to RPC: {e}")
        sys.exit(1)

    # Get chain from web3 object
    chain = get_chain_name(web3.eth.chain_id).lower()
    console.print(f"Detected chain: [blue]{chain}[/blue]")

    # Get token addresses
    try:
        token_addresses = get_tokens_address_dict(chain)
        console.print(f"Available tokens for {chain}: {list(token_addresses.keys())}")
    except Exception as e:
        console.print(f"Could not retrieve token addresses for {chain}: {e}")
        sys.exit(1)

    # Create wallet from private key
    wallet = HotWallet.from_private_key(private_key)
    wallet_address = wallet.get_main_address()

    # Sync the nonce from the blockchain
    wallet.sync_nonce(web3)
    # current_nonce = web3.eth.get_transaction_count(wallet_address)

    console.print(f"Wallet address: {wallet_address}")
    # console.print(f"Current nonce: {current_nonce}")

    # Create GMX config
    config = GMXConfig(web3, user_wallet_address=wallet_address)
    trading_client = GMXTrading(config)

    # Fetch open positions for the wallet
    console.print(f"\nFetching open positions for wallet: {wallet_address}")

    try:
        positions_fetcher = GetOpenPositions(config)
        open_positions = positions_fetcher.get_data(wallet_address)

        if not open_positions:
            console.print("[yellow]No open positions found for this wallet.[/yellow]")
            console.print("Please open a position first using gmx_open_position.py")
            sys.exit(0)

        # fancy table
        table = Table(title="Open Positions")
        table.add_column("Position Key", style="cyan")
        table.add_column("Market", style="magenta")
        table.add_column("Collateral", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Size (USD)", style="blue")
        table.add_column("Leverage", style="red")
        table.add_column("Entry Price", style="white")
        table.add_column("Mark Price", style="white")
        table.add_column("P&L %", style="white")

        for position_key, position_data in open_positions.items():
            pnl_color = "green" if position_data["percent_profit"] > 0 else "red"
            table.add_row(
                position_key,
                position_data["market_symbol"],
                position_data["collateral_token"],
                "LONG" if position_data["is_long"] else "SHORT",
                f"${position_data['position_size']:.2f}",
                f"{position_data['leverage']:.2f}x",
                f"${position_data['entry_price']:.4f}",
                f"${position_data['mark_price']:.4f}",
                f"[{pnl_color}]{position_data['percent_profit']:.2f}%[/{pnl_color}]",
            )

        console.print(table)

        # Get the first position to close
        first_position_key = list(open_positions.keys())[0]
        first_position = open_positions[first_position_key]

        console.print(f"\n[green]Closing the first position: {first_position_key}[/green]")

        # Extract parameters from the position
        market_symbol = first_position["market_symbol"]
        collateral_symbol = first_position["collateral_token"]
        is_long = first_position["is_long"]

        # Configure what percentage of the position to close (0.0 to 1.0)
        # 1.0 = close entire position, 0.5 = close 50%, etc.
        close_percentage = 0.5  # Close 50% of the position

        full_position_size = first_position["position_size"]
        size_usd = full_position_size * close_percentage  # Close partial position

        console.print(f"[blue]Position close percentage: {close_percentage * 100:.0f}%[/blue]")
        console.print(f"[blue]Full position size: ${full_position_size:.2f} USD[/blue]")
        console.print(f"[blue]Size to close: ${size_usd:.2f} USD[/blue]")

        # Calculate collateral delta from position size and leverage
        # The leverage might be incorrect in the position data, so we use a safe calculation
        leverage = first_position.get("leverage", 1.0)

        # If leverage is abnormally high (data issue), use position size / 10 as a safe estimate.
        # TODO: Temporary workaround until this leverage scaling issue is fixed
        if leverage > 100 or leverage < 0.1:
            console.print(f"[yellow]Warning: Abnormal leverage {leverage:.2f}x detected, using size/10 for collateral calculation[/yellow]")
            initial_collateral_delta = size_usd / 10
        else:
            initial_collateral_delta = size_usd / leverage

        # Ensure we have a minimum reasonable collateral value
        if initial_collateral_delta < 0.1:
            console.print(f"[yellow]Warning: Calculated collateral ${initial_collateral_delta:.4f} too small, using size as collateral[/yellow]")
            initial_collateral_delta = size_usd

        # Define reverse mapping: positions store tokens with their actual contract names (WETH, BTC)
        # but the trading API expects these symbols (ETH, WBTC)
        reverse_symbol_mapping = {
            "WETH": "ETH",
            "BTC": "WBTC",
        }

        # Apply reverse mapping to market symbol if needed
        if market_symbol in reverse_symbol_mapping:
            console.print(f"[yellow]Mapping position market symbol '{market_symbol}' to '{reverse_symbol_mapping[market_symbol]}' for trading API[/yellow]")
            market_symbol = reverse_symbol_mapping[market_symbol]

        # For simplicity, we'll use the same collateral token as the output token
        start_token_symbol = collateral_symbol

        console.print(f"\nClosing position parameters:")
        console.print(f"  Market Symbol: {market_symbol}")
        console.print(f"  Collateral Symbol: {collateral_symbol}")
        console.print(f"  Start Token Symbol: {start_token_symbol}")
        console.print(f"  Position Type: {'LONG' if is_long else 'SHORT'}")
        console.print(f"  Size to close: ${size_usd:.2f} USD")
        console.print(f"  Collateral to withdraw: ${initial_collateral_delta:.2f} USD")

    except Exception as e:
        console.print(f"[red]Error fetching positions: {str(e)}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    try:
        console.print(f"\nClosing {size_usd:.2f} USD of {'LONG' if is_long else 'SHORT'} position on {market_symbol}...")

        # Creating the close position Order object
        order = trading_client.close_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=start_token_symbol,
            is_long=is_long,
            size_delta_usd=size_usd,
            initial_collateral_delta=initial_collateral_delta,
            slippage_percent=0.005,  # 0.5% slippage
            execution_buffer=2.2,
        )

        console.print(f"\n[green]Close Position Order object created successfully![/green]")

        # Sign and send the transaction
        try:
            transaction = order.transaction
            if "nonce" in transaction:
                del transaction["nonce"]

            # Sign and send the transaction
            signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

            console.print(f"Close position transaction signed and sent!")
            console.print(f"Transaction hash: [yellow]{tx_hash.hex()}[/yellow]")

            assert_transaction_success_with_explanation(web3, tx_hash)

            # Wait for transaction receipt
            console.print("Waiting for transaction confirmation...")
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
            console.print(f"Close position transaction confirmed! Status: {receipt['status']}")
            console.print(f"Block number: {receipt['blockNumber']}")

        except Exception as e:
            console.print(f"Close position transaction failed: {str(e)}")
            console.print("This is expected if you don't have an existing position to close")
            console.print("\nTo successfully close a position, ensure:")
            console.print("   - You have an existing position on the specified market")
            console.print("   - The position type (LONG/SHORT) matches your existing position")
            console.print("   - Sufficient native token (ETH) for gas fees")
            raise e

        console.print("\n[green]GMX Position Closing completed successfully![/green]")

    except Exception as e:
        console.print(f"Error during execution: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
GMX Token Swap Script

This script demonstrates how to swap tokens on GMX protocol
using the eth_defi framework. It supports both mainnet and testnet deployments.

The script performs the following operations:

1. Connects to the specified blockchain network via RPC
2. Detects the chain from the RPC URL
3. Loads GMX contract addresses and token information
4. Creates a trading client with the provided wallet
5. Swaps tokens using GMX liquidity pools
6. Handles token approvals automatically
7. Signs and submits the transaction

Usage
-----

Basic usage with environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_swap.py

For Arbitrum mainnet::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_RPC_URL="https://arbitrum-mainnet.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_swap.py

For Avalanche mainnet::

    export PRIVATE_KEY="0x1234..."
    export AVALANCHE_RPC_URL="https://avalanche-mainnet.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_swap.py

Environment Variables
---------------------

The script requires the following environment variables:

- ``PRIVATE_KEY``: Your wallet's private key (required)
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia testnet RPC endpoint
- ``ARBITRUM_RPC_URL``: Arbitrum mainnet RPC endpoint
- ``AVALANCHE_RPC_URL``: Avalanche C-Chain mainnet RPC endpoint

Example
-------

Swap 10 USDC to WETH on Arbitrum Sepolia::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    python scripts/gmx/gmx_swap.py

Notes
-----

- The script automatically detects the chain from the RPC URL
- Token approvals are handled automatically if needed
- Swap parameters (tokens, amounts) can be modified in the script
- Ensure your wallet has sufficient input tokens and ETH for gas fees

See Also
--------

- :mod:`eth_defi.gmx.trading` - GMX trading module
- :mod:`eth_defi.gmx.config` - GMX configuration
- :class:`eth_defi.hotwallet.HotWallet` - Hot wallet implementation

"""

import logging
import os
import sys

from rich.logging import RichHandler

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.gas_monitor import GasMonitorConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import get_tokens_address_dict, get_contract_addresses, get_token_address_normalized, get_exchange_router_contract
from rich.console import Console

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

console = Console()


def main():
    # Configure logging to show gas monitoring and trading logs
    FORMAT = "%(message)s"
    logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

    # Enable logging for eth_defi modules (gas monitoring, trading, etc.)
    logging.getLogger("eth_defi").setLevel(logging.INFO)
    logging.getLogger("eth_defi.gmx.trading").setLevel(logging.INFO)
    logging.getLogger("eth_defi.gmx.gas_monitor").setLevel(logging.INFO)

    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    private_key = os.environ.get("PRIVATE_KEY")

    console.print("Starting GMX Token Swap Script...")

    # Connect to the blockchain
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

    # Get contract addresses
    try:
        contract_addresses = get_contract_addresses(chain)
        console.print(f"Retrieved contract addresses for {chain}")
    except Exception as e:
        console.print(f"Could not retrieve contract addresses for {chain}: {e}")
        sys.exit(1)

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
    current_nonce = web3.eth.get_transaction_count(wallet_address)

    console.print(f"Wallet address: {wallet_address}")
    console.print(f"Current nonce: {current_nonce}")

    # Create GMX config with gas monitoring
    config = GMXConfig(web3, user_wallet_address=wallet_address)
    gas_config = GasMonitorConfig(enabled=True)
    trading_client = GMXTrading(config, gas_monitor_config=gas_config)

    # Swap parameters
    in_token_symbol = "USDC.SG"  # Token to swap from
    out_token_symbol = "BTC"  # Token to swap to
    amount = 0.6969  # Amount in token units (e.g., 10 USDC)

    # Define the mapping for tokens that have this specific issue
    symbol_alias_mapping = {
        "WBTC": "BTC",
    }

    # Apply mapping
    in_token = symbol_alias_mapping.get(in_token_symbol.upper(), in_token_symbol.upper())
    out_token = symbol_alias_mapping.get(out_token_symbol.upper(), out_token_symbol.upper())

    console.print(f"\nSwap parameters:")
    console.print(f"  From Token: {in_token}")
    console.print(f"  To Token: {out_token}")
    console.print(f"  Amount: {amount} {in_token}")

    try:
        # Get token details for approval check
        in_token_address = get_token_address_normalized(chain, in_token)
        in_token_details = fetch_erc20_details(web3, in_token_address)
        amount_wei = int(amount * (10**in_token_details.decimals))

        console.print(f"\nSwapping {amount} {in_token} to {out_token}...")

        # Check and approve token if needed
        try:
            token_contract = in_token_details.contract
            exchange_router = get_exchange_router_contract(web3, chain)
            spender_address = exchange_router.address

            current_allowance = token_contract.functions.allowance(wallet_address, spender_address).call()
            console.print(f"Current allowance: {current_allowance / (10**in_token_details.decimals)} {in_token}")

            if current_allowance < amount_wei:
                console.print(f"Approving {in_token} tokens for GMX contract...")

                approve_tx = token_contract.functions.approve(spender_address, amount_wei * 2).build_transaction(
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

                console.print(f"Approval transaction sent! Hash: {approve_tx_hash.hex()}")
                console.print("Waiting for approval confirmation...")
                approve_receipt = web3.eth.wait_for_transaction_receipt(approve_tx_hash)
                console.print(f"Approval confirmed! Status: {approve_receipt.status}")
            else:
                console.print(f"Sufficient allowance already exists for {in_token}")

        except Exception as e:
            console.print(f"Token approval error: {str(e)}")
            console.print("Continuing with swap...")

        # Creating the swap Order object
        # Note: Pass amount in token units, not wei - OrderArgumentParser will convert to wei
        order = trading_client.swap_tokens(
            in_token_symbol=in_token,
            out_token_symbol=out_token,
            amount=amount,  # Pass in token units (e.g., 1.0 for 1 USDC)
            slippage_percent=0.03,  # 3% slippage for testnet volatility
            execution_buffer=5.0,  # Much higher buffer for testnet
        )

        console.print(f"\n[green]Swap Order object created successfully![/green]")

        # Sign and send the transaction
        try:
            transaction = order.transaction
            if "nonce" in transaction:
                del transaction["nonce"]

            # Sign and send the transaction
            signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

            console.print(f"Swap transaction signed and sent!")
            console.print(f"Transaction hash: [yellow]{tx_hash.hex()}[/yellow]")

            assert_transaction_success_with_explanation(web3, tx_hash)

            # Wait for transaction receipt
            console.print("Waiting for transaction confirmation...")
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
            console.print(f"Swap transaction confirmed! Status: {receipt['status']}")
            console.print(f"Block number: {receipt['blockNumber']}")

        except Exception as e:
            console.print(f"Swap transaction failed: {str(e)}")
            console.print("This is expected if using a test wallet without sufficient tokens or ETH")
            console.print("\nTo successfully execute swap, ensure:")
            console.print(f"   - Sufficient {in_token} token balance in wallet")
            console.print(f"   - Token approval for GMX contracts (allowance set)")
            console.print("   - Sufficient native token (ETH) for gas fees")
            raise e

        console.print("\n[green]GMX Token Swap completed successfully![/green]")

    except Exception as e:
        console.print(f"Error during execution: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

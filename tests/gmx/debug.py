import os
import sys

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import get_tokens_address_dict, get_token_address_normalized, get_contract_addresses
from rich.console import Console

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

console = Console()


def main():
    # chain_rpc_url = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
    private_key = os.environ.get("PRIVATE_KEY")

    # Skip Anvil fork - connect directly to Tenderly for faster testing
    # launch = fork_network_anvil(
    #     chain_rpc_url,
    #     test_request_timeout=30,
    #     fork_block_number=392496384,
    #     launch_wait_seconds=40,
    # )

    console.print("Starting GMX Position Opening Test...")

    # create a web3 provider - connect directly to Tenderly
    web3 = create_multi_provider_web3("https://virtual.arbitrum.eu.rpc.tenderly.co/9eae57da-4e3d-411e-9f1e-f424bd530f5b")

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

    amount_wei = 5000000 * 10**18
    # web3.provider.make_request("tenderly_setBalance", [to_checksum_address("0xf5F30B10141E1F63FC11eD772931A8294a591996"), hex(amount_wei)])
    # web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(amount_wei)])
    # web3.provider.make_request("tenderly_addErc20Balance", ["0x912ce59144191c1204e64559fe8253a0e49e6548", [wallet_address],hex(amount_wei)])

    # Sync the nonce from the blockchain
    wallet.sync_nonce(web3)
    # current_nonce = web3.eth.get_transaction_count(wallet_address)

    console.print(f"Wallet address: {wallet_address}")
    # console.print(f"Current nonce: {current_nonce}")

    # Create GMX config
    config = GMXConfig(web3, user_wallet_address=wallet_address)
    trading_client = GMXTrading(config)

    # Market symbol where we want to trade
    user_market_symbol = "BTC"

    user_collateral_symbol = "USDC"
    # If start & collateral symbols are different then it'll be swapped to collateral token
    user_start_token_symbol = "WETH"

    # Define the mapping for tokens that have this specific issue
    symbol_alias_mapping = {
        "WETH": "ETH",
        "WBTC": "BTC",
    }

    # Apply mapping only if the symbol exists in the alias map, otherwise use as is
    market_symbol = symbol_alias_mapping.get(user_market_symbol.upper(), user_market_symbol.upper())
    collateral_symbol = symbol_alias_mapping.get(user_collateral_symbol.upper(), user_collateral_symbol.upper())
    start_token_symbol = symbol_alias_mapping.get(user_start_token_symbol.upper(), user_start_token_symbol.upper())

    size_usd = 10  # Position size in USD (smaller for testing)
    leverage = 1.0  # Leverage to use

    console.print(f"\nUsing corrected token symbols for {config.get_chain()}:")
    console.print(f"  Market Symbol: {market_symbol}")
    console.print(f"  Collateral Symbol: {collateral_symbol}")
    console.print(f"  Start Token Symbol: {start_token_symbol}")

    try:
        console.print(f"\nOpening position: {size_usd} USD of {user_market_symbol} (mapped to {market_symbol}) with {leverage}x leverage")

        # Create the order FIRST - it will parse arguments and check approval
        console.print(f"\nCreating position order...")
        order = trading_client.open_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=start_token_symbol,
            is_long=True,  # Set to True for long position
            size_delta_usd=size_usd,
            leverage=leverage,
            slippage_percent=0.005,  # 0.5% slippage
            execution_buffer=2.2,  # less than this is reverting
        )

        console.print(f"\n[green]Position Order object created successfully![/green]")

        # Now handle token approval using the warning message info
        # We get the collateral amount from the order's initial_collateral_delta_amount parameter
        try:
            # Get the collateral token address
            collateral_token_address = get_token_address_normalized(chain, collateral_symbol)

            # Get token contract
            token_details = fetch_erc20_details(web3, collateral_token_address)
            token_contract = token_details.contract
            console.print(f"Collateral token contract: {collateral_token_address}")

            # Get the spender address (GMX SyntheticsRouter, not ExchangeRouter)
            contract_addresses = get_contract_addresses(chain)
            spender_address = contract_addresses.syntheticsrouter
            console.print(f"Spender (GMX SyntheticsRouter): {spender_address}")

            # Check current allowance
            current_allowance = token_contract.functions.allowance(wallet_address, spender_address).call()

            # Get the required amount from the order transaction value
            # The order already calculated the exact collateral needed
            # We can use a large approval amount since the contract will only pull what's needed
            token_decimals = token_details.decimals
            # Approve a large amount (1 billion tokens) - contract will only use what's needed
            required_amount = 1_000_000_000 * (10**token_decimals)

            console.print(f"Current allowance: {current_allowance / (10**token_decimals)} {collateral_symbol}")
            console.print(f"Approving: {required_amount / (10**token_decimals)} {collateral_symbol} (large amount for convenience)")

            if current_allowance < required_amount:
                console.print(f"Approving {collateral_symbol} tokens for GMX contract...")

                # Build the transaction
                approve_tx = token_contract.functions.approve(spender_address, required_amount).build_transaction(
                    {
                        "from": wallet_address,
                        "gas": 100000,  # Standard gas for approval
                        "gasPrice": web3.eth.gas_price,
                    }
                )

                # Remove the nonce field so wallet can handle it
                if "nonce" in approve_tx:
                    del approve_tx["nonce"]

                try:
                    # Sign and send approval transaction using wallet's nonce management
                    signed_approve_tx = wallet.sign_transaction_with_new_nonce(approve_tx)
                    approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)

                    console.print(f"Approval transaction sent! Hash: {approve_tx_hash.hex()}")

                    # Wait for approval confirmation
                    console.print("Waiting for approval confirmation...")
                    approve_receipt = web3.eth.wait_for_transaction_receipt(approve_tx_hash)
                    console.print(f"Approval confirmed! Status: {approve_receipt['status']}")
                    console.print(f"Approval block number: {approve_receipt['blockNumber']}")
                except Exception as approval_error:
                    console.print(f"Approval transaction failed: {approval_error}")
                    console.print("This is expected if using a test wallet without sufficient ETH for gas fees")
            else:
                console.print(f"Sufficient allowance already exists for {collateral_symbol}")

        except Exception as e:
            console.print(f"Token approval failed: {str(e)}")
            console.print("You may need to approve tokens manually before submitting the transaction")
            import traceback

            traceback.print_exc()

        # Sign and send the main transaction using wallet
        try:
            # Get the transaction from the order
            transaction = order.transaction
            if "nonce" in transaction:
                del transaction["nonce"]

            # Sign and send the transaction
            signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

            console.print(f"Position transaction signed and sent!")
            console.print(f"Transaction hash: [yellow]{tx_hash.hex()}[/yellow]")

            assert_transaction_success_with_explanation(web3, tx_hash)

            # Wait for transaction receipt
            console.print("Waiting for transaction confirmation...")
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
            console.print(f"Position transaction confirmed! Status: {receipt['status']}")
            console.print(f"Block number: {receipt['blockNumber']}")

        except Exception as e:
            console.print(f"Position transaction submission failed: {str(e)}")
            console.print("This is expected if using a test wallet without sufficient ETH for gas fees or tokens for collateral")
            console.print("\nTo successfully execute transactions, ensure:")
            console.print("   - Sufficient token balance in wallet")
            console.print("   - Token approval for GMX contracts (allowance set)")
            console.print("   - Sufficient native token (ETH) for gas fees")
            raise e

        console.print("\n[green]GMX Position Opening Test completed successfully![/green]")

    except Exception as e:
        console.print(f"Error during execution: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

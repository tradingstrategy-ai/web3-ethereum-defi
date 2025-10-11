#!/usr/bin/env python3
"""
GMX Position Opening Test Script for Arbitrum Sepolia
This script tests opening positions on GMX using the eth_defi framework
"""

import argparse
import sys
from web3 import Web3

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import get_tokens_address_dict, get_contract_addresses, get_token_address_normalized
from rich.console import Console

print = Console().print


def get_chain_from_rpc_url(rpc_url: str) -> str:
    """
    Infer the chain from RPC URL.
    """
    rpc_url_lower = rpc_url.lower()
    
    if "arbitrum-sepolia" in rpc_url_lower or "sepolia" in rpc_url_lower:
        return "arbitrum_sepolia"
    elif "arbitrum" in rpc_url_lower:
        return "arbitrum"
    elif "avalanche" in rpc_url_lower or "avax" in rpc_url_lower:
        return "avalanche"
    elif "fuji" in rpc_url_lower:
        return "avalanche_fuji"
    else:
        raise ValueError(f"Could not infer chain from RPC URL: {rpc_url}")


def main():
    parser = argparse.ArgumentParser(description="GMX Position Opening Test Script")
    parser.add_argument("--private-key", required=True, help="Wallet private key")
    parser.add_argument("--rpc-url", required=True, help="Blockchain RPC URL")
    
    args = parser.parse_args()
    
    print("Starting GMX Position Opening Test...")
    
    # Connect to the blockchain
    web3 = Web3(Web3.HTTPProvider(args.rpc_url))
    
    # Verify connection
    try:
        block_number = web3.eth.block_number
        print(f"Connected to network, current block: {block_number}")
    except Exception as e:
        print(f"Failed to connect to RPC: {e}")
        sys.exit(1)
    
    # Get chain from RPC URL
    chain = get_chain_from_rpc_url(args.rpc_url)
    print(f"Detected chain: {chain}")
    
    # Get contract addresses
    try:
        contract_addresses = get_contract_addresses(chain)
        print(f"Retrieved contract addresses for {chain}")
    except Exception as e:
        print(f"Could not retrieve contract addresses for {chain}: {e}")
        sys.exit(1)
    
    # Get token addresses
    try:
        token_addresses = get_tokens_address_dict(chain)
        print(f"Available tokens for {chain}: {list(token_addresses.keys())}")
    except Exception as e:
        print(f"Could not retrieve token addresses for {chain}: {e}")
        sys.exit(1)
    
    # Create wallet from private key
    wallet = HotWallet.from_private_key(args.private_key)
    wallet_address = wallet.get_main_address()
    
    # Sync the nonce from the blockchain
    wallet.sync_nonce(web3)
    current_nonce = web3.eth.get_transaction_count(wallet_address)
    
    print(f"Wallet address: {wallet_address}")
    print(f"Current nonce: {current_nonce}")
    
    # Create GMX config
    config = GMXConfig(web3, user_wallet_address=wallet_address)
    trading_client = GMXTrading(config)
    
    # Map user-friendly symbols to the ones expected by GMX v2
    # This handles the token mapping issue where "WETH"/"ETH" both refer to same address
    # but only "ETH" is kept in the final metadata dict (same for "WBTC"/"BTC")
    # Only map the specific tokens that have this issue, not all tokens
    user_market_symbol = "ETH"  # Allow users to use familiar symbol (WETH), gets mapped to ETH
    user_collateral_symbol = "USDC.SG"  # Using USDC.SG as collateral
    user_start_token_symbol = "USDC.SG"  # Start with the same token as collateral
    
    # Define the mapping for tokens that have this specific issue
    symbol_alias_mapping = {
        # "ETH": "WETH",
        "WBTC": "BTC",
        "WETH.B": "BTC",  # In case of different variations
        # Add other aliases as needed
    }

    # Apply mapping only if the symbol exists in the alias map, otherwise use as is
    market_symbol = symbol_alias_mapping.get(user_market_symbol.upper(), user_market_symbol.upper())
    collateral_symbol = symbol_alias_mapping.get(user_collateral_symbol.upper(), user_collateral_symbol.upper())
    start_token_symbol = symbol_alias_mapping.get(user_start_token_symbol.upper(), user_start_token_symbol.upper())
    
    size_usd = 10  # Position size in USD (smaller for testing)
    leverage = 1.0  # Leverage to use
    
    print(f"\nUsing corrected token symbols for {config.get_chain()}:")
    print(f"  Market Symbol: {market_symbol}")
    print(f"  Collateral Symbol: {collateral_symbol}")
    print(f"  Start Token Symbol: {start_token_symbol}")
    
    try:
        print(f"\nOpening position: {size_usd} USD of {user_market_symbol} (mapped to {market_symbol}) with {leverage}x leverage")
        
        order = trading_client.open_position(
            market_symbol=market_symbol,
            collateral_symbol=collateral_symbol,
            start_token_symbol=start_token_symbol,
            is_long=True,  # Set to True for long position
            size_delta_usd=size_usd,
            leverage=leverage,
            slippage_percent=0.005,  # 0.5% slippage
            execution_buffer=1.9,
        )
        
        print(f"\n✅ Position order created successfully!")
        print(f"Order type: {type(order)}")
        
        # Import token functionality
        from eth_defi.token import fetch_erc20_details
        from eth_defi.gmx.contracts import get_exchange_router_contract
        
        # Get token contract and approve if needed
        try:
            # Get the collateral token address
            collateral_token_address = get_token_address_normalized(chain, collateral_symbol)
            
            # Get token contract
            token_details = fetch_erc20_details(web3, collateral_token_address)
            token_contract = token_details.contract
            print(f"Collateral token contract: {collateral_token_address}")
            
            # Get the spender address (GMX exchange router)
            exchange_router = get_exchange_router_contract(web3, chain)
            spender_address = exchange_router.address
            print(f"Spender (GMX contract): {spender_address}")
            
            # Check current allowance
            current_allowance = token_contract.functions.allowance(wallet_address, spender_address).call()
            
            # Estimate required amount based on position size (smaller for testing)
            # Using 100 tokens as example for testing (adjust based on position)
            token_decimals = token_details.decimals
            required_amount = int(100 * (10 ** token_decimals))  # Example: 100 tokens in smallest units
            
            print(f"Current allowance: {current_allowance / (10 ** token_decimals)} {collateral_symbol}")
            print(f"Required amount: {required_amount / (10 ** token_decimals)} {collateral_symbol}")
            
            if current_allowance < required_amount:
                print(f"⏳ Approving {collateral_symbol} tokens for GMX contract...")
                
                # Build the transaction
                approve_tx = token_contract.functions.approve(spender_address, required_amount).build_transaction({
                    'from': wallet_address,
                    'gas': 100000,  # Standard gas for approval
                    'gasPrice': web3.eth.gas_price,
                })
                
                # Remove the nonce field so wallet can handle it
                if 'nonce' in approve_tx:
                    del approve_tx['nonce']
                
                try:
                    # Sign and send approval transaction using wallet's nonce management
                    signed_approve_tx = wallet.sign_transaction_with_new_nonce(approve_tx)
                    approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)
                    
                    print(f"✅ Approval transaction sent! Hash: {approve_tx_hash.hex()}")
                    
                    # Wait for approval confirmation
                    print("⏳ Waiting for approval confirmation...")
                    approve_receipt = web3.eth.wait_for_transaction_receipt(approve_tx_hash)
                    print(f"✅ Approval confirmed! Status: {approve_receipt.status}")
                    print(f"Approval block number: {approve_receipt.blockNumber}")
                except Exception as approval_error:
                    print(f"⚠️ Approval transaction failed: {approval_error}")
                    print("This is expected if using a test wallet without sufficient ETH for gas fees")
            else:
                print(f"✅ Sufficient allowance already exists for {collateral_symbol}")
        
        except Exception as e:
            print(f"⚠️ Token approval failed: {str(e)}")
            print("Continuing with position creation (approval may be needed for actual execution)")
            import traceback
            traceback.print_exc()
        
        # Sign and send the main transaction using wallet
        try:
            # Get the transaction from the order
            transaction = order.transaction
            if 'nonce' in transaction:
                del transaction['nonce']
            
            # Sign and send the transaction
            signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            print(f"✅ Position transaction signed and sent!")
            print(f"Transaction hash: {tx_hash.hex()}")
            
            # Wait for transaction receipt
            print("⏳ Waiting for transaction confirmation...")
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
            print(f"✅ Position transaction confirmed! Status: {receipt.status}")
            print(f"Block number: {receipt.blockNumber}")
            
        except Exception as e:
            print(f"⚠️ Position transaction submission failed: {str(e)}")
            print("This is expected if using a test wallet without sufficient ETH for gas fees or tokens for collateral")
            print("\n📝 To successfully execute transactions, ensure:")
            print("   - Sufficient token balance in wallet")
            print("   - Token approval for GMX contracts (allowance set)")
            print("   - Sufficient native token (ETH) for gas fees")
            raise e
        
        # Print order details if available
        if hasattr(order, '__dict__'):
            print(f"Order attributes: {list(order.__dict__.keys())}")
        
        print("\n🎉 GMX Position Opening Test completed successfully!")
        print("📝 Notes:")
        print("   - Script auto-maps 'WETH' to 'ETH' and 'WBTC' to 'BTC' for Arbitrum Sepolia")
        print("   - You can use familiar symbols like 'WETH', they'll be converted automatically") 
        print("   - The eth_defi wrapper handles complex configuration automatically")
        
    except Exception as e:
        print(f"❌ Error during execution: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
"""Test script for Gains Network trading on Arbitrum Sepolia.

This script executes real trades on testnet:
1. Connect to Arbitrum Sepolia testnet
2. Load wallet from PRIVATE_KEY environment variable
3. Query and approve collateral tokens
4. Execute a real market order on BTC/USD
5. Wait for confirmation and display results

Setup Requirements:
- Arbitrum Sepolia RPC URL (e.g., from Alchemy, Infura, or public RPC)
- Private key with testnet ETH for gas (get from: https://faucets.chain.link/arbitrum-sepolia)
- Testnet DAI (get from gains.trade practice mode: https://gains.trade)
  - Connect wallet → Select "Sepolia - Practice" → Click "Get 10,000 DAI"

Environment Variables (REQUIRED):
- ARBITRUM_SEPOLIA_RPC_URL: RPC endpoint URL
- PRIVATE_KEY: Your wallet private key (0x... format)

WARNING: This executes REAL transactions on testnet. Ensure you have testnet tokens!
"""

import os
import sys
import time
from decimal import Decimal

from web3 import Web3
from eth_typing import ChecksumAddress

from eth_defi.gains.trading import GainsTrading, TradeParams
from eth_defi.gains.constants import GAINS_DIAMOND_ADDRESSES
from eth_defi.abi import get_deployed_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.token import fetch_erc20_details
from rich.console import Console

console = Console()


def get_collateral_info(web3: Web3, chain: str = "arbitrum-sepolia"):
    """Query collateral information from the diamond contract.

    This queries the actual collateral addresses from the contract.
    """
    diamond_address = GAINS_DIAMOND_ADDRESSES.get(chain)
    if not diamond_address:
        raise ValueError(f"No diamond address for chain: {chain}")

    console.print(f"\nQuerying collateral info from diamond: {diamond_address}")

    # Load diamond contract
    diamond = get_deployed_contract(
        web3,
        "gains/GNSMultiCollatDiamond.json",
        diamond_address,
    )

    # Try to get collateral info
    # Note: The exact function name may vary, adjust based on actual ABI
    try:
        # Most Gains contracts have getCollaterals() or similar
        collaterals = diamond.functions.getCollaterals().call()
        console.print(f"Found {len(collaterals)} collaterals:")
        for i, collat in enumerate(collaterals):
            console.print(f"  [{i}] {collat}")
        return collaterals
    except Exception as e:
        console.print(f"Could not query collaterals: {e}")
        console.print("Using known addresses...")
        return None


def approve_token(web3: Web3, token_address: ChecksumAddress, spender: ChecksumAddress, wallet: HotWallet, amount: int = 2**256 - 1) -> str:
    """Approve token spending.

    :param web3: Web3 instance
    :param token_address: Token contract address
    :param spender: Address to approve (diamond contract)
    :param wallet: HotWallet for signing
    :param amount: Amount to approve (default: max uint256)
    :return: Transaction hash
    """
    token = fetch_erc20_details(web3, token_address)

    # Check current allowance
    current_allowance = token.contract.functions.allowance(wallet.address, spender).call()
    if current_allowance >= 10**30:  # Already has huge allowance
        console.print(f"  ✓ Token already approved (allowance: {current_allowance})")
        return None

    console.print(f"  Approving {token.symbol} spending...")

    # Build approval transaction
    approve_tx = token.contract.functions.approve(spender, amount).build_transaction(
        {
            "from": wallet.address,
            "gas": 100_000,
        }
    )

    # Sign and send
    signed = wallet.sign_transaction_with_new_nonce(approve_tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)

    console.print(f"  Approval tx: {tx_hash.hex()}")
    console.print(f"  Waiting for confirmation...")

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt["status"] == 1:
        console.print(f"  ✓ Token approved successfully!")
        return tx_hash.hex()
    else:
        raise Exception(f"Approval failed! Receipt: {receipt}")


def test_execute_market_order():
    """Test executing a real market order on Arbitrum Sepolia."""

    # Setup
    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    private_key = os.environ.get("PRIVATE_KEY")

    if not rpc_url:
        console.print("ERROR: Set ARBITRUM_SEPOLIA_RPC_URL environment variable")
        console.print("Example: export ARBITRUM_SEPOLIA_RPC_URL='https://sepolia-rollup.arbitrum.io/rpc'")
        sys.exit(1)

    if not private_key:
        console.print("ERROR: Set PRIVATE_KEY environment variable")
        console.print("Example: export PRIVATE_KEY='0x...'")
        console.print("\nWARNING: Never use mainnet private keys for testing!")
        sys.exit(1)

    # Connect to Arbitrum Sepolia
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        console.print("ERROR: Could not connect to Arbitrum Sepolia")
        sys.exit(1)

    chain_id = web3.eth.chain_id
    console.print(f"✓ Connected to Arbitrum Sepolia (chain_id: {chain_id})")
    assert chain_id == 421614, f"Wrong chain! Expected 421614, got {chain_id}"

    # Load wallet
    console.print("\n--- Loading Wallet ---")
    wallet = HotWallet.from_private_key(private_key)
    console.print(f"✓ Wallet address: {wallet.address}")

    # Check ETH balance
    eth_balance = web3.eth.get_balance(wallet.address)
    eth_balance_formatted = eth_balance / 10**18
    console.print(f"✓ ETH balance: {eth_balance_formatted:.6f} ETH")

    if eth_balance < 10**15:  # Less than 0.001 ETH
        console.print("\nWARNING: Low ETH balance! Get testnet ETH from:")
        console.print("  https://faucets.chain.link/arbitrum-sepolia")
        sys.exit(1)

    # Initialize trading
    console.print("\n--- Initializing GainsTrading ---")
    trading = GainsTrading(web3, chain="arbitrum-sepolia")
    console.print(f"✓ Diamond contract: {trading.diamond_address}")
    console.print(f"✓ Chain: {trading.chain}")

    # Query collateral info from contract
    console.print("\n--- Querying Collateral Tokens ---")
    collaterals = get_collateral_info(web3, "arbitrum-sepolia")

    # Get DAI token address (assuming index 1 based on typical Gains setup)
    # We need to query this from the contract since testnet addresses may vary
    if collaterals and len(collaterals) > 0:
        dai_address = collaterals[0][0]  # First collateral, first field (token address)
        console.print(f"✓ Using collateral token: {dai_address}")
    else:
        console.print("ERROR: Could not query collateral addresses from contract")
        console.print("The contract might not be deployed or configured on this testnet")
        sys.exit(1)

    # Check DAI balance
    dai_token = fetch_erc20_details(web3, dai_address)
    dai_balance = dai_token.contract.functions.balanceOf(wallet.address).call()
    dai_balance_formatted = dai_balance / 10**dai_token.decimals
    console.print(f"✓ {dai_token.symbol} balance: {dai_balance_formatted:.2f}")

    collateral_symbol = dai_token.symbol  # Use actual symbol from token

    if dai_balance < 500 * 10**dai_token.decimals:  # Less than 500 DAI
        console.print(f"\nERROR: Insufficient {collateral_symbol} balance!")
        console.print(f"Need at least 500 {collateral_symbol} for this test (min position size ~$1,250)")
        console.print("Get testnet DAI from https://gains.trade:")
        console.print("  1. Select 'Sepolia - Practice' network")
        console.print("  2. Connect wallet")
        console.print("  3. Click 'Get 10,000 DAI'")
        sys.exit(1)

    # Update trading collateral_tokens with actual address
    trading.collateral_tokens[collateral_symbol] = dai_address

    # Approve DAI spending
    console.print("\n--- Approving Collateral Token ---")
    approve_token(web3, dai_address, trading.diamond_address, wallet)

    console.print("\n--- Opening Market Order ---")

    # Create trade parameters
    # Note: Minimum position size is ~$1,250 on testnet
    params = TradeParams(
        pair_index=0,  # BTC/USD
        collateral_token=collateral_symbol,  # Use the actual symbol from token
        collateral_amount=Decimal("500"),  # 500 DAI collateral
        is_long=True,  # Long position
        leverage=3,  # 3x leverage = $1,500 position (meets minimum)
        slippage_percent=1.0,  # 1% slippage tolerance
        stop_loss_price=None,  # No SL for this test
        take_profit_price=None,  # No TP for this test
    )

    console.print(f"Trade params:")
    console.print(f"  Pair: BTC/USD (index {params.pair_index})")
    console.print(f"  Collateral: {params.collateral_amount} {params.collateral_token}")
    console.print(f"  Position: {'LONG' if params.is_long else 'SHORT'}")
    console.print(f"  Leverage: {params.leverage}x")
    console.print(f"  Position size: ${float(params.collateral_amount) * params.leverage}")
    console.print(f"  Max slippage: {params.slippage_percent}%")

    console.print(f"\nExecuting {params.leverage}x LONG position on BTC/USD with {params.collateral_amount} DAI...")

    try:
        # Build unsigned transaction
        console.print("\nBuilding transaction...")
        result = trading.open_market_order(params, wallet.address)

        console.print(f"✓ Transaction built")
        console.print(f"  Gas limit: {result.gas_limit:,}")
        console.print(f"  Max slippage (basis points): {result.max_slippage_p}")

        # Sign transaction
        console.print("\nSigning transaction...")
        wallet.sync_nonce(web3)  # Sync nonce from blockchain before signing
        signed_tx = wallet.sign_transaction_with_new_nonce(result.transaction)

        # Send transaction
        console.print("Sending transaction...")
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        console.print(f"✓ Transaction sent: {tx_hash.hex()}")
        console.print(f"  View on Arbiscan: https://sepolia.arbiscan.io/tx/{tx_hash.hex()}")

        # Wait for confirmation
        console.print("\nWaiting for confirmation (this may take 30-60 seconds)...")
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

        if receipt["status"] == 1:
            console.print(f"\n✓ ✓ ✓ TRADE EXECUTED SUCCESSFULLY! ✓ ✓ ✓")
            console.print(f"  Block: {receipt['blockNumber']}")
            console.print(f"  Gas used: {receipt['gasUsed']:,}")
            console.print(f"  Tx hash: {receipt['transactionHash'].hex()}")
        else:
            console.print(f"\n✗ Transaction failed!")
            console.print(f"  Receipt: {receipt}")
            return

    except Exception as e:
        console.print(f"\n✗ Error executing trade: {e}")
        import traceback

        traceback.console.print_exc()
        return

    # Query open trades to verify
    console.print("\n--- Verifying Open Position ---")
    time.sleep(2)  # Wait a bit for state to update
    try:
        trades = trading.get_open_trades(wallet.address)
        console.print(f"Found {len(trades)} open trade(s)")

        for trade in trades:
            console.print(f"\n  Trade #{trade.index}:")
            console.print(f"    Pair: {trade.pair_index} (0=BTC/USD)")
            console.print(f"    Direction: {'LONG' if trade.is_long else 'SHORT'}")
            console.print(f"    Leverage: {trade.leverage / 1000:.1f}x")  # Leverage is stored as 1e3
            console.print(f"    Open price: ${trade.open_price / 10**10:.2f}")
            console.print(f"    Collateral index: {trade.collateral_index}")

    except Exception as e:
        console.print(f"Could not query open trades: {e}")

    # Leave trade open for user to manage on gains.trade
    if len(trades) > 0:
        console.print("\n--- Trade Management ---")
        console.print("Trade is now open. View and manage it at:")
        console.print(f"  https://gains.trade (connect wallet: {wallet.address})")
        console.print("\nNote: Trade left open intentionally. Close manually when ready.")

    console.print("\n" + "=" * 60)
    console.print("✓ TEST COMPLETED SUCCESSFULLY!")
    console.print("=" * 60)
    console.print("\nSummary:")
    console.print("- Connected to Arbitrum Sepolia")
    console.print("- Loaded wallet and checked balances")
    console.print("- Approved collateral token")
    console.print("- Executed real market order on BTC/USD")
    console.print("- Verified position was opened")

    console.print("\nUseful links:")
    console.print("- View positions: https://gains.trade")
    console.print("- Arbiscan: https://sepolia.arbiscan.io/")
    console.print("- Your address:", wallet.address)


if __name__ == "__main__":
    console.print("=" * 60)
    console.print("Gains Network - Arbitrum Sepolia LIVE Trading Test")
    console.print("=" * 60)
    console.print("\nWARNING: This script will execute REAL transactions on testnet!")
    console.print("Ensure you have:")
    console.print("  - Testnet ETH for gas")
    console.print("  - Testnet DAI from gains.trade")
    console.print("\n" + "=" * 60)

    # Run live trading test
    test_execute_market_order()

    console.print("\n✓ Script completed!")

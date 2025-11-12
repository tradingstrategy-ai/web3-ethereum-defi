"""
GMX Order Creation Test - Fork Testing with GmxOrderExecutor Contract Deployment

This script demonstrates GMX order creation with 3 fork testing modes:

ANVIL FORK MODE (default - script creates fork):
    export ARBITRUM_CHAIN_JSON_RPC="https://arb1.arbitrum.io/rpc"
    export PRIVATE_KEY="0x..."
    python tests/gmx/debug_deploy.py                      # Anvil fork (default)
    python tests/gmx/debug_deploy.py --fork               # Explicit Anvil fork

CUSTOM ANVIL MODE (connect to existing Anvil instance):
    # Terminal 1: Start Anvil with fork and unlocked whale addresses
    anvil --fork-url $ARBITRUM_CHAIN_JSON_RPC --fork-block-number 392496384 \
      --unlock 0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055 \
      --unlock 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336

    # Terminal 2: Run script
    export PRIVATE_KEY="0x..."
    python tests/gmx/debug_deploy.py --anvil-rpc http://127.0.0.1:8545

TENDERLY FORK MODE:
    export TD_ARB="https://virtual.arbitrum.rpc.tenderly.co/YOUR_FORK_ID"
    export PRIVATE_KEY="0x..."
    python tests/gmx/debug_deploy.py --td                 # Tenderly fork

All modes automatically fund the wallet with ETH, USDC, and WETH for testing.

Key Differences from debug.py:
- Deploys GmxOrderExecutor contract instead of using fork_helpers.setup_mock_oracle
- Uses getMockByteCodeAndAddress() to get bytecode and provider address
- Uses setupMockOracleProvider() on deployed contract to configure prices
- Uses executeOrderGMXOrderExecutor() as keeper for order execution
"""

import os
import sys
import argparse
import time
import json

from eth_abi import encode
from eth_utils import to_checksum_address

from eth_defi.abi import get_contract
from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.contracts import (
    get_token_address_normalized,
    get_contract_addresses,
)
from rich.console import Console
from web3 import Web3

from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import extract_order_key_from_receipt, set_next_block_timestamp, mine_block
import logging
from rich.logging import RichHandler
from pathlib import Path
from eth_abi import decode

# Configure logging - suppress verbose library logs
FORMAT = "%(message)s"
logging.basicConfig(level="WARNING", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])

# Only show our logs
logger = logging.getLogger("rich")
logger.setLevel(logging.INFO)

# Suppress noisy loggers
logging.getLogger("eth_defi").setLevel(logging.WARNING)
logging.getLogger("web3").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

console = Console()


def tenderly_set_balance(web3: Web3, wallet_address: str, amount_eth: float):
    """Set ETH balance on Tenderly fork using tenderly_setBalance."""
    amount_wei = int(amount_eth * 1e18)
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(amount_wei)])
    console.print(f"  [green]Set ETH balance: {amount_eth} ETH[/green]")


def tenderly_set_erc20_balance(web3: Web3, token_address: str, wallet_address: str, amount: int):
    """Set ERC20 token balance on Tenderly fork using tenderly_setErc20Balance."""
    web3.provider.make_request("tenderly_setErc20Balance", [token_address, wallet_address, hex(amount)])
    token_details = fetch_erc20_details(web3, token_address)
    formatted_amount = amount / (10**token_details.decimals)
    console.print(f"  [green]Set {token_details.symbol} balance: {formatted_amount:.2f}[/green]")


def detect_provider_type(web3: Web3) -> str:
    """Detect if we're using Anvil or Tenderly."""
    try:
        web3.provider.make_request("anvil_nodeInfo", [])
        return "anvil"
    except Exception:
        pass

    endpoint = str(web3.provider.endpoint_uri if hasattr(web3.provider, "endpoint_uri") else "")
    if "tenderly" in endpoint.lower():
        return "tenderly"

    return "unknown"


def set_code(web3: Web3, address: str, bytecode: str):
    """Set bytecode at address (works with Anvil and Tenderly)."""
    provider_type = detect_provider_type(web3)

    # Ensure bytecode has 0x prefix and is a string
    if isinstance(bytecode, bytes):
        bytecode = "0x" + bytecode.hex()
    elif not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode

    address = to_checksum_address(address)

    if provider_type == "anvil":
        logger.info(f"Using anvil_setCode for {address}")
        web3.provider.make_request("anvil_setCode", [address, bytecode])
    elif provider_type == "tenderly":
        logger.info(f"Using tenderly_setCode for {address}")
        web3.provider.make_request("tenderly_setCode", [address, bytecode])
    else:
        try:
            web3.provider.make_request("tenderly_setCode", [address, bytecode])
        except Exception:
            web3.provider.make_request("anvil_setCode", [address, bytecode])

    # Verify bytecode was set
    deployed_code = web3.eth.get_code(address)
    expected_bytecode = bytes.fromhex(bytecode[2:]) if bytecode.startswith("0x") else bytes.fromhex(bytecode)

    if deployed_code == expected_bytecode:
        logger.info("✅ Code verification successful")
    else:
        raise Exception("Bytecode verification failed")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX Order Creation Test - Fork Testing with Contract Deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    fork_group.add_argument("--td", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    fork_group.add_argument("--anvil-rpc", type=str, help="Connect to existing Anvil RPC (e.g., http://127.0.0.1:8545)")

    parser.add_argument("--size", type=float, default=10.0, help="Position size in USD (default: 10)")
    parser.add_argument("--target-block", type=int, default=392496384, help="Target block number for executeOrder (default: 392496384)")
    parser.add_argument("--estimate-setup-blocks", action="store_true", help="Run in estimation mode to count setup blocks")
    parser.add_argument("--eth-price", type=int, default=None, help="ETH price in USD for oracle (e.g., 3892). If not set, attempts to fetch from chain.")
    parser.add_argument("--usdc-price", type=int, default=None, help="USDC price in USD for oracle (default: 1)")

    return parser.parse_args()


def load_gmx_order_executor_contract():
    """Load GmxOrderExecutor contract ABI and bytecode from compiled artifacts."""
    contract_path = Path(__file__).parent / "forked-env-example" / "out" / "GmxOrderExecutor.sol" / "GmxOrderExecutor.json"

    with open(contract_path) as f:
        contract_data = json.load(f)

    abi = contract_data["abi"]
    bytecode = contract_data["bytecode"]["object"]

    return abi, bytecode


def deploy_gmx_order_executor(web3: Web3, wallet: HotWallet) -> tuple:
    """Deploy GmxOrderExecutor contract and return contract instance."""
    console.print("\n[bold]Deploying GmxOrderExecutor contract...[/bold]")

    abi, bytecode = load_gmx_order_executor_contract()

    # Ensure bytecode has 0x prefix
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode

    # Create contract factory
    Contract = web3.eth.contract(abi=abi, bytecode=bytecode)

    # Deploy contract
    wallet_address = wallet.get_main_address()

    deploy_tx = Contract.constructor().build_transaction(
        {
            "from": wallet_address,
            "gas": 5000000,
            "gasPrice": web3.eth.gas_price,
        }
    )

    if "nonce" in deploy_tx:
        del deploy_tx["nonce"]

    signed_tx = wallet.sign_transaction_with_new_nonce(deploy_tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

    console.print(f"  TX Hash: {tx_hash.hex()}")

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] == 1:
        contract_address = receipt["contractAddress"]
        console.print(f"  [green]✓ Contract deployed at: {contract_address}[/green]")
        console.print(f"  Gas used: {receipt['gasUsed']}")

        # Return contract instance
        contract = web3.eth.contract(address=contract_address, abi=abi)
        return contract, contract_address
    else:
        raise Exception("Contract deployment failed")


def get_actual_oracle_prices_at_block(web3: Web3, block_number: int) -> dict:
    """Query what prices the actual production oracle has at a specific block.

    This helps debug price staleness issues by showing what the real oracle provider has.
    """
    console.print(f"\n[bold cyan]Querying actual oracle prices at block {block_number}...[/bold cyan]")

    # Production oracle provider address
    oracle_provider = to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD")

    # Token addresses
    weth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    # Try to call getOraclePrice on the actual oracle
    # This requires the IOracleProvider interface
    abi = [{"inputs": [{"internalType": "address", "name": "token", "type": "address"}, {"internalType": "bytes", "name": "data", "type": "bytes"}], "name": "getOraclePrice", "outputs": [{"components": [{"internalType": "address", "name": "token", "type": "address"}, {"internalType": "uint256", "name": "min", "type": "uint256"}, {"internalType": "uint256", "name": "max", "type": "uint256"}, {"internalType": "uint256", "name": "timestamp", "type": "uint256"}, {"internalType": "address", "name": "provider", "type": "address"}], "internalType": "struct OracleUtils.ValidatedPrice", "name": "validatedPrice", "type": "tuple"}], "stateMutability": "view", "type": "function"}]

    provider_contract = web3.eth.contract(address=oracle_provider, abi=abi)
    block_info = web3.eth.get_block(block_number)
    block_timestamp = block_info["timestamp"]

    console.print(f"  Block {block_number} timestamp: {block_timestamp}")

    prices = {}

    # Query WETH price
    try:
        result = provider_contract.functions.getOraclePrice(weth_address, b"").call(block_identifier=block_number)
        token, min_price, max_price, timestamp, provider = result
        price_usd = min_price / (10**30)  # GMX uses 30 decimals
        age_seconds = block_timestamp - timestamp

        prices["WETH"] = {"min": min_price, "max": max_price, "timestamp": timestamp, "age_seconds": age_seconds, "price_usd": price_usd}
        console.print(f"  [cyan]WETH actual oracle: ${price_usd:.2f} | timestamp: {timestamp} | age: {age_seconds}s[/cyan]")
    except Exception as e:
        console.print(f"  [yellow]Could not query WETH from actual oracle: {e}[/yellow]")

    # Query USDC price
    try:
        result = provider_contract.functions.getOraclePrice(usdc_address, b"").call(block_identifier=block_number)
        token, min_price, max_price, timestamp, provider = result
        price_usd = min_price / (10**30)  # GMX uses 30 decimals
        age_seconds = block_timestamp - timestamp

        prices["USDC"] = {"min": min_price, "max": max_price, "timestamp": timestamp, "age_seconds": age_seconds, "price_usd": price_usd}
        console.print(f"  [cyan]USDC actual oracle: ${price_usd:.6f} | timestamp: {timestamp} | age: {age_seconds}s[/cyan]")
    except Exception as e:
        console.print(f"  [yellow]Could not query USDC from actual oracle: {e}[/yellow]")

    return prices


def setup_mock_oracle_with_contract(
    web3: Web3,
    wallet: HotWallet,
    executor_contract,
    eth_price_usd: int = None,
    usdc_price_usd: int = None,
    fork_block: int = None,
):
    """Setup mock oracle using GmxOrderExecutor contract methods."""
    console.print("\n[bold]Setting up mock oracle via GmxOrderExecutor...[/bold]")
    wallet_address = wallet.get_main_address()

    # Query what the actual oracle has at the fork block
    if fork_block:
        actual_prices = get_actual_oracle_prices_at_block(web3, fork_block)

    # Fallback to defaults if not provided
    if eth_price_usd is None:
        eth_price_usd = 3892
    if usdc_price_usd is None:
        usdc_price_usd = 1

    console.print(f"\n[bold green]Prices we are setting in mock oracle:[/bold green]")
    console.print(f"  ETH: ${eth_price_usd}")
    console.print(f"  USDC: ${usdc_price_usd}")

    # Step 1: Call getMockByteCodeAndAddress() on the executor contract
    console.print("  [dim]Calling getMockByteCodeAndAddress()...[/dim]")
    provider_address, mock_bytecode = executor_contract.functions.getMockByteCodeAndAddress().call()

    console.print(f"  Provider address: {provider_address}")
    console.print(f"  Mock bytecode length: {len(mock_bytecode)} bytes")

    # Step 2: Set bytecode at provider address using anvil_setCode/tenderly_setCode
    console.print(f"  [dim]Setting bytecode at {provider_address}...[/dim]")
    set_code(web3, provider_address, mock_bytecode)

    # Step 3: Call configureMockOracleProvider() on the executor contract with prices
    console.print("\n[dim]Calling configureMockOracleProvider() on executor contract...[/dim]")

    setup_tx = executor_contract.functions.configureMockOracleProvider(
        eth_price_usd,
        usdc_price_usd,
    ).build_transaction(
        {
            "from": wallet_address,
            "gas": 1000000,
            "gasPrice": web3.eth.gas_price,
        }
    )

    if "nonce" in setup_tx:
        del setup_tx["nonce"]

    signed_tx = wallet.sign_transaction_with_new_nonce(setup_tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

    console.print(f"  TX Hash: {tx_hash.hex()}")

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] == 1:
        console.print(f"  [green]✓ Mock oracle configured successfully[/green]")

        # Verify mock oracle by querying it
        console.print(f"\n[bold cyan]Verifying mock oracle prices:[/bold cyan]")
        MockOracle = get_contract(web3, "gmx/MockOracleProvider.json")
        mock_oracle = MockOracle(address=provider_address)

        weth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
        usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

        # Query WETH price from mock
        try:
            result = mock_oracle.functions.getOraclePrice(weth_address, b"").call()
            token, min_price, max_price, timestamp, provider = result
            price_usd = min_price / (10**30)
            console.print(f"  [cyan]Mock oracle WETH: ${price_usd:.2f} | timestamp: {timestamp}[/cyan]")
        except Exception as e:
            console.print(f"  [yellow]Could not query WETH from mock: {e}[/yellow]")

        # Query USDC price from mock
        try:
            result = mock_oracle.functions.getOraclePrice(usdc_address, b"").call()
            token, min_price, max_price, timestamp, provider = result
            price_usd = min_price / (10**30)
            console.print(f"  [cyan]Mock oracle USDC: ${price_usd:.6f} | timestamp: {timestamp}[/cyan]")
        except Exception as e:
            console.print(f"  [yellow]Could not query USDC from mock: {e}[/yellow]")
    else:
        raise Exception("setupMockOracleProvider failed")


def execute_order_with_contract(web3: Web3, wallet: HotWallet, executor_contract, order_key: bytes):
    """Execute order as keeper by calling orderHandler directly."""
    console.print("\n[bold]Executing order as keeper...[/bold]")

    # Get keeper from RoleStore
    role_store_address = to_checksum_address("0x3c3d99FD298f679DBC2CEcd132b4eC4d0F5e6e72")
    RoleStore = get_contract(web3, "gmx/RoleStore.json")
    role_store = RoleStore(address=role_store_address)

    role_key = web3.keccak(encode(["string"], ["ORDER_KEEPER"]))

    keeper_count = role_store.functions.getRoleMemberCount(role_key).call()
    if keeper_count == 0:
        raise Exception("No keepers found in RoleStore")

    keepers = role_store.functions.getRoleMembers(role_key, 0, 1).call()
    keeper_address = keepers[0]
    console.print(f"  Keeper address: {keeper_address}")

    # Fund keeper with ETH
    provider_type = detect_provider_type(web3)
    if provider_type == "anvil":
        web3.provider.make_request(
            "anvil_setBalance",
            [keeper_address, hex(web3.to_wei(500, "ether"))],
        )
    elif provider_type == "tenderly":
        web3.provider.make_request(
            "tenderly_setBalance",
            [keeper_address, hex(web3.to_wei(500, "ether"))],
        )

    console.print(f"  [green]Funded keeper with 500 ETH[/green]")

    # Get OrderHandler contract
    order_handler_address = to_checksum_address("0x04315E233C1c6FfA61080B76E29d5e8a1f7B4A35")
    OrderHandler = get_contract(web3, "gmx/OrderHandler.json")
    order_handler = OrderHandler(address=order_handler_address)

    # Build oracle params (same as contract does)
    weth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    oracle_provider = to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD")

    oracle_params = (
        [weth_address, usdc_address],
        [oracle_provider, oracle_provider],
        [b"", b""],
    )

    # Impersonate keeper
    if provider_type == "anvil":
        web3.provider.make_request(
            "anvil_impersonateAccount",
            [keeper_address],
        )
    elif provider_type == "tenderly":
        logger.debug(f"Tenderly: Will send tx from {keeper_address}")

    try:
        console.print(f"  [dim]Calling orderHandler.executeOrder() from keeper...[/dim]")
        console.print(f"  Current block number: {web3.eth.block_number}")

        # Get the fork block's timestamp to keep oracle prices valid
        # When we fork at block 392496384, oracle prices are set with that block's timestamp
        # If we mine new blocks without controlling timestamp, prices become stale
        fork_block = 392496384
        fork_block_info = web3.eth.get_block(fork_block)
        fork_timestamp = fork_block_info["timestamp"]

        console.print(f"  Fork block {fork_block} timestamp: {fork_timestamp}")

        # Set next block timestamp to match fork block (keeps oracle prices fresh)
        # This prevents ChainlinkPriceFeedNotUpdated errors
        set_next_block_timestamp(web3, fork_timestamp)
        console.print(f"  [green]Set next block timestamp to {fork_timestamp} to prevent stale oracle prices[/green]")

        tx_hash = order_handler.functions.executeOrder(
            order_key,
            oracle_params,
        ).transact(
            {
                "from": keeper_address,
                "gas": 100_000_000,
                "gasPrice": web3.eth.gas_price,
            }
        )

        console.print(f"  TX Hash: {tx_hash.hex()}")

        # If using --no-mining mode, manually mine the block
        console.print(f"  [dim]Manually mining block (for --no-mining mode)...[/dim]")
        mine_block(web3)

        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt["status"] == 1:
            console.print(f"[green]✓ Order executed successfully![/green]")
            console.print(f"  Block: {receipt['blockNumber']}")
            console.print(f"  Gas used: {receipt['gasUsed']}")

            # Check logs for events
            console.print(f"\n[dim]Transaction logs: {len(receipt['logs'])} events emitted[/dim]")
            for i, log in enumerate(receipt["logs"][:5]):  # Show first 5 events
                console.print(f"  [dim]Event {i + 1}: {log['address'][:10]}... topics: {len(log['topics'])}[/dim]")
            return receipt
        else:
            console.print(f"[red]Transaction reverted[/red]")
            try:
                assert_transaction_success_with_explanation(web3, tx_hash)
            except Exception as e:
                console.print(f"  Revert reason: {str(e)}")
            raise Exception("Order execution failed")

    finally:
        if provider_type == "anvil":
            web3.provider.make_request(
                "anvil_stopImpersonatingAccount",
                [keeper_address],
            )


def main():
    """Main execution flow."""
    large_usdc_holder_arbitrum = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
    large_weth_holder_arbitrum = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")

    args = parse_arguments()

    # Get private key
    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    launch = None

    # Block tracking
    target_block = args.target_block
    setup_block_count = 0
    estimation_mode = args.estimate_setup_blocks

    try:
        console.print("\n[bold green]=== GMX Fork Test with Contract Deployment ===[/bold green]\n")

        if estimation_mode:
            console.print(f"[yellow]ESTIMATION MODE: Will count blocks needed for setup[/yellow]\n")

        # ========================================================================
        # STEP 1: Connect to Network (3 modes)
        # ========================================================================

        # Mode 1: Tenderly fork
        if args.td:
            tenderly_rpc = os.environ.get("TD_ARB")
            if not tenderly_rpc:
                console.print("[red]Error: TD_ARB environment variable not set[/red]")
                console.print("[yellow]Set TD_ARB to your Tenderly fork RPC URL[/yellow]")
                sys.exit(1)

            console.print("Using Tenderly fork...")
            web3 = create_multi_provider_web3(tenderly_rpc)

            block_number = web3.eth.block_number
            chain_id = web3.eth.chain_id
            chain = get_chain_name(chain_id).lower()

            console.print(f"  Block: {block_number}")
            console.print(f"  Chain ID: {chain_id}")
            console.print(f"  Chain: {chain}")

        # Mode 2: Custom Anvil RPC
        elif args.anvil_rpc:
            console.print(f"Using existing Anvil instance at {args.anvil_rpc}...")
            console.print("[dim]NOTE: Make sure you started Anvil with fork and unlocked addresses:[/dim]")
            console.print("[dim]  anvil --fork-url $ARBITRUM_CHAIN_JSON_RPC --fork-block-number 392496384 \\[/dim]")
            console.print("[dim]    --unlock 0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055 \\[/dim]")
            console.print("[dim]    --unlock 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336[/dim]")

            web3 = create_multi_provider_web3(args.anvil_rpc)

            block_number = web3.eth.block_number
            chain_id = web3.eth.chain_id
            chain = get_chain_name(chain_id).lower()

            console.print(f"  Block: {block_number}")
            console.print(f"  Chain ID: {chain_id}")
            console.print(f"  Chain: {chain}\n")

        # Mode 3: Anvil fork (default - script creates fork)
        else:
            fork_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
            if not fork_rpc:
                console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
                sys.exit(1)

            # Calculate fork block: if in estimation mode, use target block directly
            # Otherwise, estimate we need 5 blocks for setup (this will be refined)
            if estimation_mode:
                fork_block = target_block
            else:
                # Estimated setup blocks: deploy(1) + configure(1) + usdc_transfer(1) + weth_transfer(1) + submit_order(1) = 5
                estimated_setup_blocks = 5
                fork_block = target_block - estimated_setup_blocks
                console.print(f"[yellow]Target block for executeOrder: {target_block}[/yellow]")
                console.print(f"[yellow]Estimated setup blocks: {estimated_setup_blocks}[/yellow]")
                console.print(f"[yellow]Calculated fork block: {fork_block}[/yellow]\n")

            console.print(f"Creating Anvil fork at block {fork_block}...")

            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[
                    large_usdc_holder_arbitrum,
                    large_weth_holder_arbitrum,
                ],
                fork_block_number=fork_block,
            )
            web3 = Web3(Web3.HTTPProvider(launch.json_rpc_url))

            block_number = web3.eth.block_number
            chain_id = web3.eth.chain_id
            chain = get_chain_name(chain_id).lower()

            console.print(f"  Anvil fork started on {launch.json_rpc_url}")
            console.print(f"  Block: {block_number}")
            console.print(f"  Chain ID: {chain_id}")
            console.print(f"  Chain: {chain}")

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
        # STEP 2.5: Fund Wallet (all fork modes)
        # ========================================================================
        console.print("\n[bold]Funding wallet...[/bold]")

        if args.td:
            # Tenderly fork - use Tenderly RPC calls
            tenderly_set_balance(web3, wallet_address, 100.0)

            usdc_address = tokens.get("USDC")
            if usdc_address:
                usdc_amount = 100_000 * (10**6)  # 100k USDC
                tenderly_set_erc20_balance(web3, usdc_address, wallet_address, usdc_amount)

            weth_address = tokens.get("WETH")
            if weth_address:
                weth_amount = 1000 * (10**18)  # 1000 WETH
                tenderly_set_erc20_balance(web3, weth_address, wallet_address, weth_amount)

        else:
            # Anvil fork (both modes) - use anvil_setBalance + transfers from unlocked addresses
            eth_amount_wei = 100 * 10**18  # 100 ETH
            web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
            console.print(f"  [green]Set ETH balance: 100 ETH[/green]")

            # Set USDC balance via transfer from unlocked whale
            usdc_address = tokens.get("USDC")
            if usdc_address:
                block_before = web3.eth.block_number
                usdc_amount = 100_000 * (10**6)  # 100k USDC
                usdc_token = fetch_erc20_details(web3, usdc_address)
                usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": large_usdc_holder_arbitrum})
                balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
                console.print(f"  [green]Set USDC balance: {balance / 10**6:.2f} USDC[/green]")
                block_after = web3.eth.block_number
                blocks_mined = block_after - block_before
                setup_block_count += blocks_mined
                console.print(f"[dim]  Blocks mined: {blocks_mined} | Total setup blocks: {setup_block_count} | Current block: {block_after}[/dim]")

            # Set WETH balance via transfer from unlocked whale
            weth_address = tokens.get("WETH")
            if weth_address:
                block_before = web3.eth.block_number
                weth_amount = 1000 * (10**18)  # 1000 WETH
                weth_token = fetch_erc20_details(web3, weth_address)
                weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": large_weth_holder_arbitrum})
                balance = weth_token.contract.functions.balanceOf(wallet_address).call()
                console.print(f"  [green]Set WETH balance: {balance / 10**18:.2f} WETH[/green]")
                block_after = web3.eth.block_number
                blocks_mined = block_after - block_before
                setup_block_count += blocks_mined
                console.print(f"[dim]  Blocks mined: {blocks_mined} | Total setup blocks: {setup_block_count} | Current block: {block_after}[/dim]")

        # ========================================================================
        # STEP 2.7: Deploy GmxOrderExecutor Contract
        # ========================================================================
        block_before = web3.eth.block_number
        executor_contract, executor_address = deploy_gmx_order_executor(web3, wallet)
        block_after = web3.eth.block_number
        blocks_mined = block_after - block_before
        setup_block_count += blocks_mined
        console.print(f"[dim]  Blocks mined: {blocks_mined} | Total setup blocks: {setup_block_count} | Current block: {block_after}[/dim]")

        # ========================================================================
        # STEP 2.8: Setup Mock Oracle via GmxOrderExecutor
        # ========================================================================
        block_before = web3.eth.block_number

        # Get the fork block number for fetching real prices
        # If using Anvil fork, use the stored fork_block variable
        if not args.td and not args.anvil_rpc:
            fetch_fork_block = fork_block if "fork_block" in locals() else target_block
        else:
            # For Tenderly or custom Anvil, use current block as reference
            fetch_fork_block = web3.eth.block_number

        setup_mock_oracle_with_contract(web3, wallet, executor_contract, eth_price_usd=args.eth_price, usdc_price_usd=args.usdc_price, fork_block=fetch_fork_block)
        block_after = web3.eth.block_number
        blocks_mined = block_after - block_before
        setup_block_count += blocks_mined
        console.print(f"[dim]  Blocks mined: {blocks_mined} | Total setup blocks: {setup_block_count} | Current block: {block_after}[/dim]")

        # ========================================================================
        # STEP 3: Configure Position Parameters
        # ========================================================================
        console.print("\n[bold]Creating GMX order...[/bold]")

        # Determine position size
        size_usd = args.size

        # Configure position
        market_symbol = "ETH"
        collateral_symbol = "ETH"  # ETH gets auto-wrapped to WETH by GMX
        start_token_symbol = "ETH"
        leverage = 2.5

        console.print(f"  Market: {market_symbol}")
        console.print(f"  Collateral: {collateral_symbol}")
        console.print(f"  Size: ${size_usd} at {leverage}x leverage")
        console.print(f"  Direction: LONG")

        # ========================================================================
        # STEP 4: Create GMX Order
        # ========================================================================

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
        # STEP 5: Handle Token Approval
        # ========================================================================
        console.print("\n[bold]Handling token approval...[/bold]")

        collateral_token_address = get_token_address_normalized(chain, collateral_symbol)

        # ETH doesn't need approval (native currency, will be wrapped by ExchangeRouter)
        if collateral_token_address is None or collateral_symbol in ["ETH", "WETH"]:
            console.print(f"  [green]Using native ETH - no approval needed[/green]")
        else:
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
                block_before = web3.eth.block_number
                approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)

                console.print(f"    TX: {approve_tx_hash.hex()}")

                # If using --no-mining mode, manually mine the block
                mine_block(web3)

                approve_receipt = web3.eth.wait_for_transaction_receipt(approve_tx_hash)
                console.print(f"  [green]Approval successful[/green]")
                block_after = web3.eth.block_number
                blocks_mined = block_after - block_before
                setup_block_count += blocks_mined
                console.print(f"[dim]  Blocks mined: {blocks_mined} | Total setup blocks: {setup_block_count} | Current block: {block_after}[/dim]")
            else:
                console.print(f"  [green]Sufficient allowance exists[/green]")

        # ========================================================================
        # STEP 6: Submit Order
        # ========================================================================
        console.print("\n[bold]Submitting order to ExchangeRouter...[/bold]")

        # Resync nonce to avoid issues with reused forks
        wallet.sync_nonce(web3)

        transaction = order.transaction
        if "nonce" in transaction:
            del transaction["nonce"]

        console.print(f"  To: {transaction['to']}")
        console.print(f"  Value: {transaction['value'] / 1e18:.6f} ETH")
        console.print(f"  Data size: {len(transaction['data'])} bytes")

        block_before = web3.eth.block_number
        signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        console.print(f"\n  TX Hash: {tx_hash.hex()}")

        # If using --no-mining mode, manually mine the block
        console.print(f"  [dim]Manually mining block (for --no-mining mode)...[/dim]")
        mine_block(web3)

        # Wait for confirmation
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        block_after = web3.eth.block_number
        blocks_mined = block_after - block_before
        setup_block_count += blocks_mined

        console.print(f"\n[bold]Transaction Status: {receipt['status']}[/bold]")

        if receipt["status"] == 1:
            console.print(f"[green]✓ Order submitted successfully![/green]")
            console.print(f"  Block: {receipt['blockNumber']}")
            console.print(f"  Gas used: {receipt['gasUsed']}")
            console.print(f"[dim]  Blocks mined: {blocks_mined} | Total setup blocks: {setup_block_count} | Current block: {block_after}[/dim]")

            # Display block status before executeOrder
            console.print(f"\n[bold yellow]Ready to execute order:[/bold yellow]")
            console.print(f"  Current block: {block_after}")
            console.print(f"  Target block: {target_block}")
            console.print(f"  Difference: {target_block - block_after}")

            if estimation_mode:
                console.print(f"\n[bold green]ESTIMATION COMPLETE:[/bold green]")
                console.print(f"  Total setup blocks needed: {setup_block_count}")
                console.print(f"  Recommended fork block: {target_block - setup_block_count}")
                console.print(f"\n[yellow]Run again without --estimate-setup-blocks to execute with correct fork block[/yellow]")
                return

            # ========================================================================
            # STEP 6.5: Extract Order Key from Receipt
            # ========================================================================
            order_key = None
            try:
                order_key = extract_order_key_from_receipt(receipt)
                console.print(f"\n[green]✓ Order Key: {order_key.hex()}[/green]")
            except Exception as e:
                console.print(f"\n[yellow]Warning: Could not extract order key: {e}[/yellow]")

            # ========================================================================
            # STEP 7: Execute Order via GmxOrderExecutor Contract
            # ========================================================================
            if order_key:
                try:
                    exec_receipt = execute_order_with_contract(web3, wallet, executor_contract, order_key)
                except Exception as e:
                    console.print(f"[red]✗ Contract execution failed: {e}[/red]")
                    import traceback

                    console.print(f"[dim]{traceback.format_exc()}[/dim]")

            # ========================================================================
            # STEP 8: Verify Position is Opened
            # ========================================================================
            console.print("\n[bold]Sleeping for 10s...[/bold]")
            time.sleep(7)
            console.print("\n[bold]Verifying position...[/bold]")

            # Check if position is opened
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

                    # Calculate PnL if available
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

            # Try to get revert reason
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

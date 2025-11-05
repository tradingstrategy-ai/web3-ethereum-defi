#!/usr/bin/env python3
"""
GMX Order Creation Test (Fixed Timestamp Version)
Supports:
  - Anvil fork (default)
  - Tenderly fork (--td)
  - Custom Anvil RPC (--anvil-rpc)
"""

import os
import sys
import argparse
import time
import json
from pathlib import Path
import logging
from rich.console import Console
from rich.logging import RichHandler
from eth_utils import to_checksum_address
from eth_abi import encode
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.hotwallet import HotWallet
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.token import fetch_erc20_details
from eth_defi.gmx.contracts import get_token_address_normalized, get_contract_addresses
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import extract_order_key_from_receipt
from eth_defi.abi import get_contract

# ============ Logging ============
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, handlers=[RichHandler()])
logger = logging.getLogger("rich")
console = Console()

# ============================================================
# Helper utilities for time + block control
# ============================================================

def _detect_provider_type(web3):
    try:
        web3.provider.make_request("anvil_nodeInfo", [])
        return "anvil"
    except Exception:
        pass
    endpoint = str(getattr(web3.provider, "endpoint_uri", "") or "")
    if "tenderly" in endpoint.lower():
        return "tenderly"
    return "unknown"


def _set_next_block_timestamp(web3, ts: int):
    """Cross-compatible block timestamp setter."""
    try:
        web3.provider.make_request("evm_setNextBlockTimestamp", [ts])
        return
    except Exception:
        pass

    provider = _detect_provider_type(web3)
    try:
        if provider == "anvil":
            web3.provider.make_request("anvil_setNextBlockTimestamp", [ts])
            return
        elif provider == "tenderly":
            web3.provider.make_request("tenderly_setNextBlockTimestamp", [ts])
            return
    except Exception:
        pass

    now = web3.eth.get_block("latest")["timestamp"]
    if ts > now:
        web3.provider.make_request("evm_increaseTime", [ts - now])


def _mine_block(web3):
    """Mine a block across providers."""
    try:
        web3.provider.make_request("evm_mine", [])
        return
    except Exception:
        pass

    provider = _detect_provider_type(web3)
    if provider == "anvil":
        web3.provider.make_request("anvil_mine", [])
    elif provider == "tenderly":
        web3.provider.make_request("tenderly_mine", [])


# ============================================================
# Contract loading + deployment
# ============================================================

def load_gmx_order_executor_contract():
    path = Path(__file__).parent / "forked-env-example" / "out" / "GmxOrderExecutor.sol" / "GmxOrderExecutor.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing. Compile it first.")
    with open(path) as f:
        data = json.load(f)
    return data["abi"], data["bytecode"]["object"]


def set_code(web3, address: str, bytecode: str):
    provider_type = _detect_provider_type(web3)
    if isinstance(bytecode, bytes):
        bytecode = "0x" + bytecode.hex()
    elif not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode
    address = to_checksum_address(address)
    if provider_type == "anvil":
        web3.provider.make_request("anvil_setCode", [address, bytecode])
    elif provider_type == "tenderly":
        web3.provider.make_request("tenderly_setCode", [address, bytecode])
    else:
        web3.provider.make_request("evm_setCode", [address, bytecode])


def deploy_gmx_order_executor(web3, wallet):
    abi, bytecode = load_gmx_order_executor_contract()
    Contract = web3.eth.contract(abi=abi, bytecode=bytecode)
    tx = Contract.constructor().build_transaction({
        "from": wallet.get_main_address(),
        "gas": 5_000_000,
        "gasPrice": web3.eth.gas_price,
    })
    signed = wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] != 1:
        raise RuntimeError("Contract deployment failed")
    console.print(f"[green]✓ Deployed GmxOrderExecutor at {receipt.contractAddress}[/green]")
    return web3.eth.contract(address=receipt.contractAddress, abi=abi)


def setup_mock_oracle(web3, wallet, executor_contract, eth_price=3892, usdc_price=1):
    console.print("\n[bold]Setting up mock oracle...[/bold]")
    wallet_addr = wallet.get_main_address()

    provider_addr, mock_bytecode = executor_contract.functions.getMockByteCodeAndAddress().call()
    set_code(web3, provider_addr, mock_bytecode)
    console.print(f"[green]✓ Mock provider bytecode set at {provider_addr}[/green]")

    # --- Timestamp fix ---
    ts = web3.eth.get_block("latest")["timestamp"]
    _set_next_block_timestamp(web3, ts)
    _mine_block(web3)

    tx = executor_contract.functions.configureMockOracleProvider(eth_price, usdc_price).build_transaction({
        "from": wallet_addr,
        "gas": 1_000_000,
        "gasPrice": web3.eth.gas_price,
    })
    signed = wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] != 1:
        raise RuntimeError("configureMockOracleProvider failed")
    console.print(f"[green]✓ Mock oracle configured: ETH=${eth_price}, USDC=${usdc_price}[/green]")

    _mine_block(web3)
    console.print("[dim]Mined block after oracle configuration[/dim]")


# ============================================================
# CLI Arguments
# ============================================================

def parse_arguments():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    group.add_argument("--td", action="store_true", help="Use Tenderly fork")
    group.add_argument("--anvil-rpc", type=str, help="Use existing Anvil RPC")

    parser.add_argument("--size", type=float, default=10.0)
    parser.add_argument("--target-block", type=int, default=392496384)
    parser.add_argument("--estimate-setup-blocks", action="store_true")
    return parser.parse_args()


# ============================================================
# Main
# ============================================================

def main():
    args = parse_arguments()
    private_key = os.environ.get("PRIVATE_KEY")
    if not private_key:
        console.print("[red]PRIVATE_KEY not set[/red]")
        sys.exit(1)

    large_usdc_holder = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
    large_weth_holder = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")

    console.print("\n[bold green]=== GMX Fork Test (Timestamp Safe) ===[/bold green]\n")

    if args.td:
        rpc = os.environ.get("TD_ARB")
        if not rpc:
            console.print("[red]TD_ARB not set[/red]")
            sys.exit(1)
        web3 = create_multi_provider_web3(rpc)
        console.print("[yellow]Connected to Tenderly Fork[/yellow]")
    elif args.anvil_rpc:
        web3 = create_multi_provider_web3(args.anvil_rpc)
        console.print(f"[yellow]Connected to Anvil RPC at {args.anvil_rpc}[/yellow]")
    else:
        fork_block = args.target_block - 5
        launch = fork_network_anvil(
            os.environ["ARBITRUM_CHAIN_JSON_RPC"],
            unlocked_addresses=[large_usdc_holder, large_weth_holder],
            fork_block_number=fork_block,
        )
        web3 = Web3(Web3.HTTPProvider(launch.json_rpc_url))
        console.print(f"[green]Created fork at block {fork_block}[/green]")

    # Wallet setup
    wallet = HotWallet.from_private_key(private_key)
    wallet.sync_nonce(web3)
    wallet_addr = wallet.get_main_address()
    web3.provider.make_request("anvil_setBalance", [wallet_addr, hex(100 * 10**18)])
    console.print(f"Wallet: {wallet_addr}")

    # Deploy + setup oracle
    executor = deploy_gmx_order_executor(web3, wallet)
    setup_mock_oracle(web3, wallet, executor)

    # Create & submit GMX order
    config = GMXConfig(web3, user_wallet_address=wallet_addr)
    trading_client = GMXTrading(config)
    order = trading_client.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=args.size,
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=2.2,
    )
    console.print(f"[green]Order created, mark price: {order.mark_price}[/green]")

    # Submit
    wallet.sync_nonce(web3)
    tx = order.transaction
    if "nonce" in tx:
        del tx["nonce"]
    signed = wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    _mine_block(web3)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    console.print(f"Order submit status: {receipt['status']}")

    # Extract key + execute
    from tests.gmx.fork_helpers import extract_order_key_from_receipt
    order_key = extract_order_key_from_receipt(receipt)
    console.print(f"Order key: {order_key.hex()}")

    # Execute order (as keeper)
    order_handler = get_contract(web3, "gmx/OrderHandler.json")(
        address=to_checksum_address("0x04315E233C1c6FfA61080B76E29d5e8a1f7B4A35")
    )

    fork_block = args.target_block
    ts = web3.eth.get_block(fork_block)["timestamp"]
    _set_next_block_timestamp(web3, ts)
    _mine_block(web3)

    oracle_params = (
        [to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"),
         to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")],
        [to_checksum_address("0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD")] * 2,
        [b"", b""],
    )

    tx = order_handler.functions.executeOrder(order_key, oracle_params).build_transaction({
        "from": wallet_addr,
        "gas": 5_000_000,
        "gasPrice": web3.eth.gas_price,
    })
    signed = wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    _mine_block(web3)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    console.print(f"[green]Order executed successfully! Block {receipt.blockNumber}[/green]")

    # Verify open position
    verifier = GetOpenPositions(config)
    positions = verifier.get_data(wallet_addr)
    if positions:
        for key, pos in positions.items():
            console.print(f"[green]Position opened: {pos['position_size']} USD at {pos['entry_price']}[/green]")
    else:
        console.print("[yellow]No open positions found[/yellow]")


if __name__ == "__main__":
    main()

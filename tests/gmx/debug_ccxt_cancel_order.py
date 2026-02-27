"""GMX CCXT cancel order lifecycle debug script - fork testing

Reproduces the exact lifecycle from test_ccxt_cancel_order_lifecycle:

1. Create Anvil fork (or connect to Tenderly / existing Anvil)
2. Setup mock oracle
3. Create wallet, fund with ETH/WETH/USDC
4. Create GMXConfig, approve tokens for both routers
5. Create CCXT GMX exchange, load markets via RPC
6. Open ETH long with bundled stop-loss via create_market_buy_order()
7. Execute position order as keeper (SL stays pending)
8. fetch_orders() returns the pending SL
9. cancel_order(sl_key_hex) cancels the SL
10. fetch_orders() returns empty

MODES:
1. Anvil fork (default):   python tests/gmx/debug_ccxt_cancel_order.py --fork
2. Tenderly fork:          python tests/gmx/debug_ccxt_cancel_order.py --td
3. Custom Anvil RPC:       python tests/gmx/debug_ccxt_cancel_order.py --anvil-rpc http://localhost:8545

Required environment variables:
- PRIVATE_KEY: Private key for signing transactions (default: Anvil account 0)
- ARBITRUM_CHAIN_JSON_RPC: RPC endpoint for Anvil fork
- TD_ARB: Tenderly fork URL (for --td mode)
"""

import argparse
import logging
import os
import sys
import time

from eth_utils import to_checksum_address
from rich.console import Console
from rich.logging import RichHandler
from web3 import Web3

from eth_defi.chain import get_chain_name, install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import NETWORK_TOKENS, get_contract_addresses, get_token_address_normalized
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from tests.gmx.fork_helpers import (
    execute_order_as_keeper,
    extract_order_key_from_receipt,
    setup_mock_oracle,
)

# Configure logging
FORMAT = "%(message)s"
logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
logger = logging.getLogger("rich")

console = Console()

# Fork test configuration
ANVIL_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
LARGE_USDC_HOLDER = to_checksum_address("0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055")
LARGE_WETH_HOLDER = to_checksum_address("0x70d95587d40A2caf56bd97485aB3Eec10Bee6336")
GMX_KEEPER_ADDRESS = to_checksum_address("0x7452c558d45f8afc8c83dae62c3f8a5be19c71f6")
EXECUTION_BUFFER = 30

#: Event topic hash for ``OrderCreated(bytes32,OrderProps)``
ORDER_CREATED_HASH = "a7427759bfd3b941f14e687e129519da3c9b0046c5b9aaa290bb1dede63753b3"


def setup_fork_network(web3: Web3):
    """Setup mock oracle and display network info.

    Follows GMX forked-env-example pattern:
    - Fetches actual on-chain oracle prices before mocking
    - This ensures prices pass GMX's validation
    """
    block_number = web3.eth.block_number
    chain_id = web3.eth.chain_id
    chain = get_chain_name(chain_id).lower()

    console.print(f"  Block: {block_number}")
    console.print(f"  Chain ID: {chain_id}")
    console.print(f"  Chain: {chain}")

    # Setup mock oracle - prices fetched dynamically from chain
    console.print("\n[dim]Setting up mock oracle (fetching on-chain prices)...[/dim]")
    setup_mock_oracle(web3)
    console.print(f"[dim]Mock oracle configured with on-chain prices[/dim]\n")

    return chain


def fund_wallet_anvil(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Anvil fork using anvil_setBalance and whale transfers.

    Matches the test fixture ``_fund_wallet_on_fork`` amounts (100M each).
    """
    console.print("\n[bold]Funding wallet (Anvil mode)...[/bold]")

    # Match test fixture: 100_000_000 ETH (tests/gmx/ccxt/conftest.py:86)
    eth_amount_wei = 100_000_000 * 10**18
    web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]ETH balance: 100,000,000 ETH (matching test fixture)[/green]")

    # Give whales some ETH for gas (match test fixture)
    gas_eth = 100_000_000 * 10**18
    web3.provider.make_request("anvil_setBalance", [LARGE_USDC_HOLDER, hex(gas_eth)])
    web3.provider.make_request("anvil_setBalance", [LARGE_WETH_HOLDER, hex(gas_eth)])

    # Fund GMX keeper for order execution
    web3.provider.make_request("anvil_setBalance", [GMX_KEEPER_ADDRESS, hex(gas_eth)])
    console.print(f"  [green]GMX Keeper funded: 100,000,000 ETH[/green]")

    # Transfer USDC from whale (match test fixture: 100M USDC)
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000_000 * (10**6)
        usdc_token = fetch_erc20_details(web3, usdc_address)
        usdc_token.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": LARGE_USDC_HOLDER})
        balance = usdc_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]USDC balance: {balance / 10**6:.2f} USDC[/green]")

    # Transfer WETH from whale (match test fixture: 100M WETH)
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 100_000_000 * (10**18)
        weth_token = fetch_erc20_details(web3, weth_address)
        weth_token.contract.functions.transfer(wallet_address, weth_amount).transact({"from": LARGE_WETH_HOLDER})
        balance = weth_token.contract.functions.balanceOf(wallet_address).call()
        console.print(f"  [green]WETH balance: {balance / 10**18:.2f} WETH[/green]")


def fund_wallet_tenderly(web3: Web3, wallet_address: str, tokens: dict):
    """Fund wallet on Tenderly fork using Tenderly RPC methods."""
    console.print("\n[bold]Funding wallet (Tenderly mode)...[/bold]")

    # Set ETH balance
    eth_amount_wei = 100 * 10**18
    web3.provider.make_request("tenderly_setBalance", [wallet_address, hex(eth_amount_wei)])
    console.print(f"  [green]ETH balance: 100 ETH[/green]")

    # Fund GMX keeper for order execution
    gas_eth = 1 * 10**18
    web3.provider.make_request("tenderly_setBalance", [GMX_KEEPER_ADDRESS, hex(gas_eth)])
    console.print(f"  [green]GMX Keeper funded: 1 ETH[/green]")

    # Set USDC balance
    usdc_address = tokens.get("USDC")
    if usdc_address:
        usdc_amount = 100_000 * (10**6)
        web3.provider.make_request("tenderly_setErc20Balance", [usdc_address, wallet_address, hex(usdc_amount)])
        console.print(f"  [green]USDC balance: 100,000 USDC[/green]")

    # Set WETH balance (needed for ETH/USDC market with ETH collateral)
    weth_address = tokens.get("WETH")
    if weth_address:
        weth_amount = 1000 * (10**18)
        web3.provider.make_request("tenderly_setErc20Balance", [weth_address, wallet_address, hex(weth_amount)])
        console.print(f"  [green]WETH balance: 1,000 WETH[/green]")


def approve_tokens_for_routers(web3: Web3, wallet_address: str, chain_name: str):
    """Approve tokens for GMX routers (syntheticsrouter and exchangerouter).

    Reproduces the ``_approve_tokens_for_config`` pattern from
    ``tests/gmx/conftest.py``.
    """
    console.print("\n[bold]Approving tokens for GMX routers...[/bold]")

    tokens = NETWORK_TOKENS[chain_name]
    token_addresses = [tokens["USDC"], tokens["WETH"], tokens["WBTC"], tokens["USDT"], tokens["LINK"]]

    contract_addresses = get_contract_addresses(chain_name)
    router_addresses = [contract_addresses.syntheticsrouter, contract_addresses.exchangerouter]

    wallet_address_checksum = to_checksum_address(wallet_address)
    max_uint256 = 2**256 - 1

    for token_addr in token_addresses:
        try:
            token_details = fetch_erc20_details(web3, token_addr)
            for router_address in router_addresses:
                try:
                    token_details.contract.functions.approve(router_address, max_uint256).transact({"from": wallet_address_checksum})
                except Exception:
                    pass
            console.print(f"  [green]{token_details.symbol} approved[/green]")
        except Exception:
            pass


def verify_orders_created(receipt: dict) -> list[bytes]:
    """Extract order keys from transaction receipt."""
    order_keys = []

    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue

        topic_hashes = []
        for topic in topics:
            if isinstance(topic, bytes):
                topic_hashes.append(topic.hex())
            elif isinstance(topic, str):
                topic_hex = topic[2:] if topic.startswith("0x") else topic
                topic_hashes.append(topic_hex)
            else:
                topic_hashes.append(topic.hex())

        if len(topic_hashes) >= 2 and topic_hashes[1] == ORDER_CREATED_HASH:
            order_key = bytes.fromhex(topic_hashes[2])
            order_keys.append(order_key)

    return order_keys


def log_balances(web3: Web3, wallet_address: str, tokens: dict, label: str, keeper_address: str = None):
    """Log ETH, WETH, and USDC balances with gas price info.

    Uses both web3 API and raw RPC ``eth_getBalance`` to detect middleware
    caching issues.

    :param web3:
        Web3 instance.
    :param wallet_address:
        Wallet address to check balances for.
    :param tokens:
        Dict mapping symbol -> address (e.g. ``{"WETH": "0x...", "USDC": "0x..."}``).
    :param label:
        Descriptive label for the log section.
    :param keeper_address:
        Optional keeper address to also check balance for.
    """
    console.print(f"\n  [dim]--- Balances: {label} ---[/dim]")

    # ETH balance via web3 API
    eth_balance_wei = web3.eth.get_balance(wallet_address)
    eth_balance = eth_balance_wei / 10**18
    console.print(f"    ETH (web3):   {eth_balance:.6f} ({eth_balance_wei} wei)")

    # ETH balance via raw RPC (bypass middleware)
    try:
        raw_result = web3.provider.make_request("eth_getBalance", [wallet_address, "latest"])
        raw_balance_hex = raw_result.get("result", "0x0")
        raw_balance_wei = int(raw_balance_hex, 16)
        raw_balance = raw_balance_wei / 10**18
        console.print(f"    ETH (raw RPC): {raw_balance:.6f} ({raw_balance_wei} wei)")
        if raw_balance_wei != eth_balance_wei:
            console.print(f"    [red]MISMATCH: web3={eth_balance_wei} vs raw={raw_balance_wei}[/red]")
    except Exception as e:
        console.print(f"    ETH (raw RPC): [red]error: {e}[/red]")

    # Keeper balance if provided
    if keeper_address:
        try:
            keeper_eth = web3.eth.get_balance(keeper_address)
            console.print(f"    Keeper ETH:   {keeper_eth / 10**18:.6f} ({keeper_eth} wei)")
        except Exception:
            pass

    # Gas price
    try:
        gas_price = web3.eth.gas_price
        console.print(f"    Gas price: {gas_price / 10**9:.4f} gwei")
    except Exception:
        pass

    # ERC-20 token balances
    for symbol, address in tokens.items():
        if not address:
            continue
        try:
            token = fetch_erc20_details(web3, address)
            raw_balance = token.contract.functions.balanceOf(wallet_address).call()
            decimals = token.decimals
            human_balance = raw_balance / (10**decimals)
            console.print(f"    {symbol}: {human_balance:,.{min(decimals, 6)}f}")
        except Exception as e:
            console.print(f"    {symbol}: [red]error reading balance: {e}[/red]")

    # Nonce
    nonce = web3.eth.get_transaction_count(wallet_address)
    console.print(f"    Nonce (on-chain): {nonce}")
    console.print(f"  [dim]--- end balances ---[/dim]")


def display_orders(orders: list, label: str):
    """Display CCXT order details in a structured format."""
    console.print(f"\n  [bold]{label}[/bold]")
    if not orders:
        console.print("    [yellow]No orders found[/yellow]")
        return

    for i, order in enumerate(orders, 1):
        console.print(f"    Order #{i}:")
        console.print(f"      ID:     {order.get('id', 'N/A')}")
        console.print(f"      Type:   {order.get('type', 'N/A')}")
        console.print(f"      Side:   {order.get('side', 'N/A')}")
        price = order.get("price", 0)
        if price and price > 0:
            console.print(f"      Price:  ${price:,.2f}")
        else:
            console.print(f"      Price:  {price}")
        console.print(f"      Status: {order.get('status', 'N/A')}")
        info = order.get("info", {})
        if info.get("order_key"):
            console.print(f"      Key:    {info['order_key']}")
        if info.get("is_long") is not None:
            console.print(f"      Long:   {info['is_long']}")


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GMX CCXT cancel order lifecycle debug script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Fork mode options (mutually exclusive)
    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument("--fork", action="store_true", help="Create Anvil fork (default)")
    fork_group.add_argument("--td", action="store_true", help="Use Tenderly fork (requires TD_ARB env var)")
    fork_group.add_argument("--anvil-rpc", type=str, help="Connect to existing Anvil RPC (e.g., http://127.0.0.1:8545)")

    # Position parameters
    parser.add_argument("--size", type=float, default=10.0, help="Position size in USD (default: 10)")
    parser.add_argument("--sl-percent", type=float, default=0.05, help="Stop loss percentage (default: 0.05 = 5%%)")

    return parser.parse_args()


def main():
    """Main execution flow."""
    args = parse_arguments()

    private_key = os.environ.get("PRIVATE_KEY", ANVIL_PRIVATE_KEY)

    launch = None
    is_tenderly = False

    # Get wallet address from private key early (needed for Anvil unlocking)
    temp_wallet = HotWallet.from_private_key(private_key)
    wallet_address = temp_wallet.get_main_address()

    try:
        console.print("\n[bold green]=== GMX CCXT Cancel Order Lifecycle Debug ===[/bold green]\n")

        # ====================================================================
        # STEP 1: Connect to network
        # ====================================================================

        if args.td:
            tenderly_rpc = os.environ.get("TD_ARB")
            if not tenderly_rpc:
                console.print("[red]Error: TD_ARB environment variable not set[/red]")
                sys.exit(1)

            console.print("Using Tenderly fork...")
            web3 = create_multi_provider_web3(tenderly_rpc)
            is_tenderly = True

        elif args.anvil_rpc:
            console.print(f"Using custom Anvil at {args.anvil_rpc}...")
            web3 = create_multi_provider_web3(args.anvil_rpc, default_http_timeout=(3.0, 180.0))

        else:
            fork_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
            if not fork_rpc:
                console.print("[red]Error: ARBITRUM_CHAIN_JSON_RPC environment variable not set[/red]")
                sys.exit(1)

            launch = fork_network_anvil(
                fork_rpc,
                unlocked_addresses=[
                    wallet_address,
                    LARGE_USDC_HOLDER,
                    LARGE_WETH_HOLDER,
                    GMX_KEEPER_ADDRESS,
                ],
            )

            web3 = create_multi_provider_web3(
                launch.json_rpc_url,
                default_http_timeout=(3.0, 180.0),
            )
            console.print(f"  Anvil fork started on {launch.json_rpc_url}")

        # Match the test fixture: install middleware and gas strategy
        install_chain_middleware(web3)
        web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)

        chain = setup_fork_network(web3)

        # ====================================================================
        # STEP 2: Setup wallet
        # ====================================================================
        console.print("\n[bold]Setting up wallet...[/bold]")
        wallet = temp_wallet
        wallet.sync_nonce(web3)
        console.print(f"  Wallet: {wallet_address}")

        tokens = {
            "WETH": get_token_address_normalized(chain, "WETH"),
            "USDC": get_token_address_normalized(chain, "USDC"),
        }
        for symbol, address in tokens.items():
            console.print(f"  {symbol}: {address}")

        # ====================================================================
        # STEP 3: Fund wallet
        # ====================================================================
        if is_tenderly:
            fund_wallet_tenderly(web3, wallet_address, tokens)
        else:
            fund_wallet_anvil(web3, wallet_address, tokens)

        # ====================================================================
        # STEP 4: Create GMXConfig and approve tokens
        # ====================================================================
        console.print("\n[bold]Setting up GMXConfig and token approvals...[/bold]")
        config = GMXConfig(web3, user_wallet_address=wallet_address)
        approve_tokens_for_routers(web3, wallet_address, chain)

        # Sync nonce AFTER approve transactions — _approve_tokens_for_config()
        # uses transact() which increments the on-chain nonce without going
        # through HotWallet's internal counter
        wallet.sync_nonce(web3)
        console.print(f"  Nonce synced: {wallet.current_nonce}")

        # ====================================================================
        # STEP 5: Create CCXT GMX exchange
        # ====================================================================
        console.print("\n[bold]Creating CCXT GMX exchange...[/bold]")

        rpc_url = web3.provider.endpoint_uri if hasattr(web3.provider, "endpoint_uri") else None
        gmx = GMX(params={"rpcUrl": rpc_url, "wallet": wallet})

        console.print("  Loading markets (RPC mode)...")
        gmx.load_markets(params={"rest_api_mode": False, "graphql_only": False})
        console.print(f"  [green]Loaded {len(gmx.markets)} markets[/green]")

        # ====================================================================
        # STEP 6: Create market buy order with bundled stop-loss
        # ====================================================================
        symbol = "ETH/USDC:USDC"
        size_usd = args.size
        sl_percent = args.sl_percent

        console.print("\n[bold cyan]Step 1: Creating market buy order with bundled stop-loss...[/bold cyan]")
        console.print(f"  Symbol:     {symbol}")
        console.print(f"  Size:       ${size_usd}")
        console.print("  Leverage:   2.5x")
        console.print("  Collateral: ETH")
        console.print(f"  Stop Loss:  {sl_percent * 100:.1f}%")

        log_balances(web3, wallet_address, tokens, "BEFORE create_market_buy_order")

        order = gmx.create_market_buy_order(
            symbol,
            0,
            {
                "size_usd": size_usd,
                "leverage": 2.5,
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
                "execution_buffer": EXECUTION_BUFFER,
                "wait_for_execution": False,
                "stopLoss": {
                    "triggerPercent": sl_percent,
                    "closePercent": 1.0,
                },
            },
        )

        assert order is not None, "create_market_buy_order returned None"

        log_balances(web3, wallet_address, tokens, "AFTER create_market_buy_order")

        tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
        assert tx_hash is not None, "Order must have a transaction hash"
        console.print("  [green]Order created[/green]")
        console.print(f"  TX Hash: {tx_hash}")
        console.print(f"  Status:  {order.get('status', 'unknown')}")

        # ====================================================================
        # STEP 7: Execute position order as keeper (SL stays pending)
        # ====================================================================
        console.print("\n[bold cyan]Step 2: Executing position order as keeper...[/bold cyan]")

        if isinstance(tx_hash, str):
            tx_hash_bytes = bytes.fromhex(tx_hash[2:]) if tx_hash.startswith("0x") else bytes.fromhex(tx_hash)
        else:
            tx_hash_bytes = tx_hash

        receipt = web3.eth.wait_for_transaction_receipt(tx_hash_bytes)

        if receipt["status"] != 1:
            console.print("[red]Order transaction failed[/red]")
            assert_transaction_success_with_explanation(web3, tx_hash_bytes)
            sys.exit(1)

        console.print("  [green]Order transaction successful[/green]")

        # Extract order keys from receipt
        order_keys = verify_orders_created(receipt)
        if order_keys:
            console.print("\n  Orders created in receipt:")
            for idx, key in enumerate(order_keys, 1):
                order_type = ["Main (Increase)", "Stop Loss", "Take Profit"][idx - 1] if idx <= 3 else f"Order {idx}"
                console.print(f"    {order_type}: 0x{key.hex()}")

        # Execute main order as keeper
        main_order_key = order_keys[0]
        console.print("\n  Executing main order as keeper...")

        log_balances(web3, wallet_address, tokens, "BEFORE execute_order_as_keeper")

        try:
            exec_receipt, keeper_address = execute_order_as_keeper(web3, main_order_key)

            log_balances(web3, wallet_address, tokens, "AFTER execute_order_as_keeper", keeper_address=keeper_address)

            console.print(f"  [green]Main order executed by keeper {keeper_address}[/green]")
            console.print(f"  Block: {exec_receipt['blockNumber']}")
            console.print(f"  Gas used: {exec_receipt['gasUsed']}")
        except Exception as e:
            console.print(f"[red]Keeper execution failed: {e}[/red]")
            import traceback

            traceback.print_exc()
            sys.exit(1)

        # Re-fund wallet — execute_order_as_keeper drains wallet ETH on
        # Anvil forks (same workaround used in debug_ccxt.py:419)
        console.print("\n  [yellow]Re-funding wallet after keeper execution...[/yellow]")
        eth_refund = 100_000_000 * 10**18
        web3.provider.make_request("anvil_setBalance", [wallet_address, hex(eth_refund)])
        wallet.sync_nonce(web3)

        log_balances(web3, wallet_address, tokens, "AFTER re-fund")

        # ====================================================================
        # STEP 8: Fetch pending orders (expect SL)
        # ====================================================================
        console.print("\n[bold cyan]Step 3: Fetching pending orders (expect SL)...[/bold cyan]")
        time.sleep(1)  # Brief wait for state to settle

        pending = gmx.fetch_orders(symbol=symbol)
        display_orders(pending, "Pending orders after position execution")

        assert len(pending) >= 1, f"Expected at least 1 pending order, got {len(pending)}"
        sl_order = pending[0]
        sl_key_hex = sl_order["id"]
        console.print(f"\n  [green]Found pending SL order: {sl_key_hex}[/green]")

        # Validate CCXT structure
        assert sl_order.get("status") == "open", f"Expected status='open', got {sl_order.get('status')!r}"
        assert sl_order.get("type") == "stopLoss", f"Expected type='stopLoss', got {sl_order.get('type')!r}"
        assert sl_order.get("side") == "buy", f"Expected side='buy' for long SL, got {sl_order.get('side')!r}"
        assert sl_order.get("price", 0) > 0, "SL trigger price must be non-zero"
        console.print("  [green]SL order structure validated (status=open, type=stop_loss, side=buy)[/green]")

        # ====================================================================
        # STEP 9: Cancel the SL order
        # ====================================================================
        console.print(f"\n[bold cyan]Step 4: Cancelling SL order {sl_key_hex[:26]}...[/bold cyan]")

        log_balances(web3, wallet_address, tokens, "BEFORE cancel_order")

        cancel_result = gmx.cancel_order(sl_key_hex, symbol=symbol)

        log_balances(web3, wallet_address, tokens, "AFTER cancel_order")

        console.print("  Cancel result:")
        console.print(f"    Status:  {cancel_result.get('status')}")
        console.print(f"    ID:      {cancel_result.get('id')}")
        cancel_tx_hash = cancel_result.get("info", {}).get("tx_hash")
        console.print(f"    TX Hash: {cancel_tx_hash}")

        assert cancel_result.get("status") == "cancelled", f"Expected status='cancelled', got {cancel_result.get('status')!r}"
        assert cancel_result.get("id") == sl_key_hex, "Cancelled order id must match the requested key"
        assert cancel_result["info"].get("tx_hash") is not None, "Cancel result must include tx_hash"
        console.print("  [green]Order cancelled successfully[/green]")

        # ====================================================================
        # STEP 10: Verify order is gone
        # ====================================================================
        console.print("\n[bold cyan]Step 5: Verifying order is gone...[/bold cyan]")

        pending_after = gmx.fetch_orders(symbol=symbol)
        display_orders(pending_after, "Pending orders after cancellation")

        cancelled_keys = [o["id"] for o in pending_after]
        assert sl_key_hex not in cancelled_keys, f"Cancelled SL key {sl_key_hex[:18]}... must not appear in fetch_orders() after cancellation"
        console.print("\n  [green]Confirmed: cancelled order no longer in pending list[/green]")

        # ====================================================================
        # SUMMARY
        # ====================================================================
        console.print("\n" + "=" * 60)
        console.print("[bold green]Cancel order lifecycle test PASSED[/bold green]")
        console.print("=" * 60)
        console.print("\nLifecycle completed:")
        console.print("  1. Position with bundled SL created")
        console.print("  2. Position order executed by keeper")
        console.print("  3. Pending SL order found via fetch_orders()")
        console.print("  4. SL order cancelled via cancel_order()")
        console.print("  5. Confirmed order removed from pending list")
        console.print(f"\n  Create TX: {tx_hash}")
        console.print(f"  Cancel TX: {cancel_tx_hash}")
        console.print(f"  Order key: {sl_key_hex}")

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

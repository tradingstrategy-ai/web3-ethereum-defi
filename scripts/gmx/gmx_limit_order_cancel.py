"""
GMX Limit Order Cancel Script for Arbitrum Sepolia

Opens a short limit order on the ETH market with trigger price set to 10 % above
the current oracle spot price, verifies it is pending in the DataStore, then
immediately cancels it. Useful for testing the full
create → inspect → cancel lifecycle end-to-end.

A short limit order triggers when the oracle price **rises** to the trigger
level, so setting the trigger 10 % above the current spot guarantees the
order will never execute spontaneously during the test — it simply sits in
the DataStore until we cancel it.

Flow
----

1. Connect to Arbitrum Sepolia and verify wallet balance.
2. Fetch the current ETH oracle price from the GMX signed-prices API.
3. Set trigger = oracle_price × 1.10 (10 % above spot).
4. Approve USDC.SG collateral for the GMX SyntheticsRouter (if not already).
5. Open a short limit order with the computed trigger price.
6. Extract the order key from the ``OrderCreated`` event in the receipt.
7. Verify the order appears via :func:`~eth_defi.gmx.order.pending_orders.fetch_pending_orders`.
8. Build and submit a ``cancelOrder`` transaction.
9. Confirm the order is no longer pending.

Usage
-----

With environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    poetry run python scripts/gmx/gmx_limit_order_cancel.py

Environment Variables
---------------------

- ``PRIVATE_KEY``: Wallet private key (required).
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia JSON-RPC endpoint (required).
- ``TRIGGER_PRICE_USD``: Override the trigger price in USD (optional).
  If not set, the price is automatically computed as 10 % above the current
  oracle spot price.

See Also
--------

- :meth:`eth_defi.gmx.trading.GMXTrading.open_limit_position`
- :meth:`eth_defi.gmx.trading.GMXTrading.cancel_order`
- :func:`eth_defi.gmx.order.pending_orders.fetch_pending_orders`
"""

import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler

from eth_defi.chain import get_chain_name
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_token_address_normalized
from eth_defi.gmx.gas_monitor import GasMonitorConfig
from eth_defi.gmx.order.pending_orders import fetch_pending_orders
from eth_defi.gmx.trading import GMXTrading
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from scripts.gmx.script_utils import fetch_eth_spot_price

console = Console()

# ---------------------------------------------------------------------------
# Defaults — override via environment variables
# ---------------------------------------------------------------------------
MARKET_SYMBOL = "ETH"
COLLATERAL_SYMBOL = "USDC.SG"
START_TOKEN_SYMBOL = "USDC.SG"
SIZE_USD = 10
LEVERAGE = 2.0
SLIPPAGE_PERCENT = 0.005
EXECUTION_BUFFER = 30

#: Event topic hash for ``OrderCreated(bytes32,OrderProps)``
ORDER_CREATED_TOPIC = "a7427759bfd3b941f14e687e129519da3c9b0046c5b9aaa290bb1dede63753b3"


def _extract_order_keys(receipt: dict) -> list[bytes]:
    """Return the order keys emitted by ``OrderCreated`` events in *receipt*.

    :param receipt:
        Transaction receipt dict from ``web3.eth.wait_for_transaction_receipt``.
    :return:
        List of 32-byte order keys found in the receipt.
    """
    keys: list[bytes] = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        topic_hashes = []
        for topic in topics:
            if isinstance(topic, bytes):
                topic_hashes.append(topic.hex())
            elif isinstance(topic, str):
                topic_hashes.append(topic.removeprefix("0x"))
            else:
                topic_hashes.append(topic.hex())
        if len(topic_hashes) >= 3 and topic_hashes[1] == ORDER_CREATED_TOPIC:
            keys.append(bytes.fromhex(topic_hashes[2]))
    return keys


def main():
    FORMAT = "%(message)s"
    logging.basicConfig(level="INFO", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()])
    logging.getLogger("eth_defi").setLevel(logging.INFO)

    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    private_key = os.environ.get("PRIVATE_KEY")
    trigger_price_override = os.environ.get("TRIGGER_PRICE_USD")

    if not rpc_url:
        console.print("[red]Error: ARBITRUM_SEPOLIA_RPC_URL environment variable not set[/red]")
        sys.exit(1)

    if not private_key:
        console.print("[red]Error: PRIVATE_KEY environment variable not set[/red]")
        sys.exit(1)

    console.print("\n[bold green]=== GMX Limit Order + Cancel — Arbitrum Sepolia ===[/bold green]\n")

    # ------------------------------------------------------------------
    # Step 1: Connect
    # ------------------------------------------------------------------
    web3 = create_multi_provider_web3(rpc_url)

    try:
        block_number = web3.eth.block_number
        chain_id = web3.eth.chain_id
        chain = get_chain_name(chain_id).lower()
        console.print(f"Connected to [bold]{chain}[/bold] (chain_id={chain_id}, block={block_number})")
    except Exception as exc:
        console.print(f"[red]Failed to connect: {exc}[/red]")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2: Wallet setup
    # ------------------------------------------------------------------
    wallet = HotWallet.from_private_key(private_key)
    wallet_address = wallet.get_main_address()
    wallet.sync_nonce(web3)

    eth_balance = web3.eth.get_balance(wallet_address)
    console.print(f"\nWallet: [yellow]{wallet_address}[/yellow]")
    console.print(f"  ETH balance: {eth_balance / 10**18:.6f} ETH")

    if eth_balance == 0:
        console.print("[red]Wallet has no ETH for execution fees — top up and retry.[/red]")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 3: Fetch oracle price and compute trigger
    # ------------------------------------------------------------------
    if trigger_price_override:
        trigger_price_usd = float(trigger_price_override)
        console.print(f"\nUsing manual trigger price: ${trigger_price_usd:,.2f} (from TRIGGER_PRICE_USD env var)")
    else:
        console.print("\nFetching current ETH oracle price from GMX API…")
        try:
            spot_price = fetch_eth_spot_price(chain)
            trigger_price_usd = spot_price * 1.10
            console.print(f"  Oracle spot price:  ${spot_price:,.2f}")
            console.print(f"  Trigger price (+10%): ${trigger_price_usd:,.2f}")
        except Exception as exc:
            console.print(f"[red]Could not fetch oracle price: {exc}[/red]")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 4: Token approval
    # ------------------------------------------------------------------
    collateral_token_address = get_token_address_normalized(chain, COLLATERAL_SYMBOL)
    if collateral_token_address:
        token_details = fetch_erc20_details(web3, collateral_token_address)
        contract_addresses = get_contract_addresses(chain)
        spender = contract_addresses.syntheticsrouter

        token_balance = token_details.contract.functions.balanceOf(wallet_address).call()
        current_allowance = token_details.contract.functions.allowance(wallet_address, spender).call()
        required_amount = 1_000_000_000 * (10**token_details.decimals)

        console.print(f"\n{COLLATERAL_SYMBOL} balance: {token_balance / 10**token_details.decimals:.2f}")
        console.print(f"{COLLATERAL_SYMBOL} allowance: {current_allowance / 10**token_details.decimals:.2f}")

        if current_allowance < required_amount:
            console.print(f"Approving [bold]{COLLATERAL_SYMBOL}[/bold] for SyntheticsRouter…")
            approve_tx = token_details.contract.functions.approve(spender, required_amount).build_transaction(
                {
                    "from": wallet_address,
                    "gas": 100_000,
                    "gasPrice": web3.eth.gas_price,
                }
            )
            if "nonce" in approve_tx:
                del approve_tx["nonce"]
            signed_approve = wallet.sign_transaction_with_new_nonce(approve_tx)
            approve_hash = web3.eth.send_raw_transaction(signed_approve.rawTransaction)
            console.print(f"  Approval TX: [yellow]{approve_hash.hex()}[/yellow]")
            web3.eth.wait_for_transaction_receipt(approve_hash)
            console.print("  [green]Approved.[/green]")
        else:
            console.print(f"  [green]Sufficient allowance — no approval needed.[/green]")
    else:
        console.print(f"[yellow]Could not resolve {COLLATERAL_SYMBOL} address on {chain} — skipping approval.[/yellow]")

    # ------------------------------------------------------------------
    # Step 5: Create short limit order (trigger 10 % above spot)
    # ------------------------------------------------------------------
    #
    # A short limit order triggers when oracle_price >= trigger_price.
    # Since trigger_price is set 10 % ABOVE the current spot, the condition
    # is currently FALSE — the order stays pending until ETH rises 10 %,
    # which gives us time to test the cancel flow.
    #
    config = GMXConfig(web3, user_wallet_address=wallet_address)
    gas_config = GasMonitorConfig(enabled=True)
    trading = GMXTrading(config, gas_monitor_config=gas_config)

    console.print(f"\n[bold cyan]Step 1 — Create short limit order (trigger 10 % above spot)[/bold cyan]")
    console.print(f"  Market:        {MARKET_SYMBOL}")
    console.print(f"  Collateral:    {COLLATERAL_SYMBOL}")
    console.print(f"  Direction:     SHORT")
    console.print(f"  Size:          ${SIZE_USD}")
    console.print(f"  Leverage:      {LEVERAGE}x")
    console.print(f"  Trigger price: ${trigger_price_usd:,.2f}  (10 % above spot — order stays pending)")

    order_result = trading.open_limit_position(
        market_symbol=MARKET_SYMBOL,
        collateral_symbol=COLLATERAL_SYMBOL,
        start_token_symbol=START_TOKEN_SYMBOL,
        is_long=False,
        size_delta_usd=SIZE_USD,
        leverage=LEVERAGE,
        trigger_price=trigger_price_usd,
        slippage_percent=SLIPPAGE_PERCENT,
        execution_buffer=EXECUTION_BUFFER,
    )

    console.print(f"  Execution fee: {order_result.execution_fee / 10**18:.6f} ETH")

    transaction = dict(order_result.transaction)
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    console.print(f"  TX: [yellow]{tx_hash.hex()}[/yellow]")

    assert_transaction_success_with_explanation(web3, tx_hash)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    console.print(f"  Confirmed in block {receipt['blockNumber']}.")

    order_keys = _extract_order_keys(receipt)
    if not order_keys:
        console.print("[red]No OrderCreated event found in receipt — cannot determine order key.[/red]")
        sys.exit(1)

    # The first emitted order key is the limit increase order
    order_key = order_keys[0]
    console.print(f"  [green]Order key: 0x{order_key.hex()}[/green]")

    # ------------------------------------------------------------------
    # Step 6: Verify order is pending
    # ------------------------------------------------------------------
    console.print(f"\n[bold cyan]Step 2 — Verify order is pending in DataStore[/bold cyan]")

    pending = list(fetch_pending_orders(web3, chain, wallet_address))
    matching = [o for o in pending if o.order_key == order_key]

    if not matching:
        console.print("[yellow]Order not found in pending list — it may have already executed or been cancelled.[/yellow]")
        sys.exit(0)

    order_info = matching[0]
    console.print(f"  Found pending order:")
    console.print(f"    Type:          {order_info.order_type.name}")
    console.print(f"    Is long:       {order_info.is_long}")
    console.print(f"    Size (USD):    ${order_info.size_delta_usd_human:.2f}")
    console.print(f"    Trigger price: ${order_info.trigger_price_usd:,.2f}")
    console.print(f"    Execution fee: {order_info.execution_fee / 10**18:.6f} ETH")

    # ------------------------------------------------------------------
    # Step 7: Cancel the order
    # ------------------------------------------------------------------
    console.print(f"\n[bold cyan]Step 3 — Cancel limit order[/bold cyan]")

    cancel_result = trading.cancel_order(order_key)

    cancel_tx = dict(cancel_result.transaction)
    if "nonce" in cancel_tx:
        del cancel_tx["nonce"]

    signed_cancel = wallet.sign_transaction_with_new_nonce(cancel_tx)
    cancel_hash = web3.eth.send_raw_transaction(signed_cancel.rawTransaction)
    console.print(f"  Cancel TX: [yellow]{cancel_hash.hex()}[/yellow]")

    assert_transaction_success_with_explanation(web3, cancel_hash)
    cancel_receipt = web3.eth.wait_for_transaction_receipt(cancel_hash)
    console.print(f"  Confirmed in block {cancel_receipt['blockNumber']}.")

    # ------------------------------------------------------------------
    # Step 8: Confirm the order is gone
    # ------------------------------------------------------------------
    console.print(f"\n[bold cyan]Step 4 — Confirm cancellation[/bold cyan]")

    pending_after = list(fetch_pending_orders(web3, chain, wallet_address))
    still_there = [o for o in pending_after if o.order_key == order_key]

    if still_there:
        console.print("[red]Order still appears in DataStore — cancellation may have failed.[/red]")
        sys.exit(1)

    console.print(f"  [green]Order 0x{order_key.hex()[:16]}… is no longer pending.[/green]")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    console.print("\n" + "=" * 60)
    console.print("[bold green]Limit order create → cancel lifecycle complete![/bold green]")
    console.print("=" * 60)
    console.print(f"\n  Create TX:  {tx_hash.hex()}")
    console.print(f"  Cancel TX:  {cancel_hash.hex()}")
    console.print(f"  Order key:  0x{order_key.hex()}")


if __name__ == "__main__":
    main()

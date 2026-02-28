"""
GMX CCXT Limit Order Cancel Script for Arbitrum Sepolia

Demonstrates the full create → inspect → cancel lifecycle using the
:class:`~eth_defi.gmx.ccxt.exchange.GMX` CCXT-compatible wrapper.

Opens a short limit order on the ETH market with trigger price set to
10 % above the current oracle spot price, verifies it appears in
:meth:`~eth_defi.gmx.ccxt.exchange.GMX.fetch_orders`, then cancels it
via :meth:`~eth_defi.gmx.ccxt.exchange.GMX.cancel_order` and confirms
the order list is empty afterwards.

Flow
----

1. Connect to Arbitrum Sepolia using the GMX CCXT exchange.
2. Fetch the current ETH oracle price.
3. Set trigger = oracle_price × 1.10 (10 % above spot).
4. Create a short limit order via ``gmx.trader.open_limit_position()``.
5. Sign and submit the transaction using the wallet attached to the exchange.
6. Verify the order appears in ``gmx.fetch_orders()``.
7. Cancel the order via ``gmx.cancel_order(order_key_hex)``.
8. Confirm ``gmx.fetch_orders()`` returns an empty list.

Usage
-----

With environment variables::

    export PRIVATE_KEY="0x1234..."
    export ARBITRUM_SEPOLIA_RPC_URL="https://arbitrum-sepolia.infura.io/v3/YOUR_KEY"
    poetry run python scripts/gmx/gmx_ccxt_limit_order_cancel.py

Environment Variables
---------------------

- ``PRIVATE_KEY``: Wallet private key (required).
- ``ARBITRUM_SEPOLIA_RPC_URL``: Arbitrum Sepolia JSON-RPC endpoint (required).
- ``TRIGGER_PRICE_USD``: Override the trigger price in USD (optional).
  If not set, the price is computed as 10 % above the current oracle spot price.

See Also
--------

- :meth:`eth_defi.gmx.ccxt.exchange.GMX.cancel_order`
- :meth:`eth_defi.gmx.ccxt.exchange.GMX.fetch_orders`
- :meth:`eth_defi.gmx.trading.GMXTrading.open_limit_position`
"""

import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.hotwallet import HotWallet
from eth_defi.trace import assert_transaction_success_with_explanation
from scripts.gmx.script_utils import fetch_eth_spot_price

console = Console()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
MARKET_SYMBOL = "ETH/USDC:USDC"
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

    console.print("\n[bold green]=== GMX CCXT Limit Order + Cancel — Arbitrum Sepolia ===[/bold green]\n")

    # ------------------------------------------------------------------
    # Step 1: Create CCXT GMX exchange
    # ------------------------------------------------------------------
    console.print("[bold cyan]Step 1 — Connect via CCXT GMX exchange[/bold cyan]")

    wallet = HotWallet.from_private_key(private_key)

    gmx = GMX(
        params={
            "rpcUrl": rpc_url,
            "wallet": wallet,
        }
    )

    gmx.load_markets(params={"rest_api_mode": False, "graphql_only": False})

    chain_id = gmx.web3.eth.chain_id
    chain = get_chain_name(chain_id).lower()
    wallet_address = wallet.address
    wallet.sync_nonce(gmx.web3)

    eth_balance = gmx.web3.eth.get_balance(wallet_address)
    console.print(f"  Chain:         [bold]{chain}[/bold] (chain_id={chain_id})")
    console.print(f"  Wallet:        [yellow]{wallet_address}[/yellow]")
    console.print(f"  ETH balance:   {eth_balance / 10**18:.6f} ETH")

    if eth_balance == 0:
        console.print("[red]Wallet has no ETH for execution fees — top up and retry.[/red]")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2: Fetch oracle price and compute trigger
    # ------------------------------------------------------------------
    console.print(f"\n[bold cyan]Step 2 — Fetch oracle price[/bold cyan]")

    if trigger_price_override:
        trigger_price_usd = float(trigger_price_override)
        console.print(f"  Using manual trigger: ${trigger_price_usd:,.2f} (from TRIGGER_PRICE_USD env var)")
    else:
        try:
            spot_price = fetch_eth_spot_price(chain)
            trigger_price_usd = spot_price * 1.10
            console.print(f"  Oracle spot price:    ${spot_price:,.2f}")
            console.print(f"  Trigger (+10 %):      ${trigger_price_usd:,.2f}")
        except Exception as exc:
            console.print(f"[red]Could not fetch oracle price: {exc}[/red]")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 3: Create short limit order via trading module
    # ------------------------------------------------------------------
    #
    # We use gmx.trader (a GMXTrading instance) to build the unsigned
    # transaction, then sign and submit it ourselves so we can extract
    # the order key from the receipt before proceeding.
    #
    # A short limit order triggers when oracle_price >= trigger_price.
    # Setting trigger 10 % above spot keeps the order pending during our test.
    #
    console.print(f"\n[bold cyan]Step 3 — Create short limit order[/bold cyan]")
    console.print(f"  Market:        ETH (short)")
    console.print(f"  Collateral:    {COLLATERAL_SYMBOL}")
    console.print(f"  Size:          ${SIZE_USD}")
    console.print(f"  Leverage:      {LEVERAGE}x")
    console.print(f"  Trigger price: ${trigger_price_usd:,.2f}  (10 % above spot — stays pending)")

    order_result = gmx.trader.open_limit_position(
        market_symbol="ETH",
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

    signed_tx = gmx.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = gmx.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    console.print(f"  TX: [yellow]{tx_hash.hex()}[/yellow]")

    assert_transaction_success_with_explanation(gmx.web3, tx_hash)
    receipt = gmx.web3.eth.wait_for_transaction_receipt(tx_hash)
    console.print(f"  Confirmed in block {receipt['blockNumber']}.")

    order_keys = _extract_order_keys(receipt)
    if not order_keys:
        console.print("[red]No OrderCreated event found in receipt — cannot determine order key.[/red]")
        sys.exit(1)

    order_key_bytes = order_keys[0]
    order_key_hex = "0x" + order_key_bytes.hex()
    console.print(f"  [green]Order key: {order_key_hex}[/green]")

    # ------------------------------------------------------------------
    # Step 4: Verify via CCXT fetch_orders
    # ------------------------------------------------------------------
    console.print(f"\n[bold cyan]Step 4 — Verify via CCXT fetch_orders()[/bold cyan]")

    pending = gmx.fetch_orders(symbol=MARKET_SYMBOL)

    matching = [o for o in pending if o.get("id") == order_key_hex]
    if not matching:
        console.print("[yellow]Order not found in fetch_orders() — it may have already executed.[/yellow]")
        sys.exit(0)

    order_info = matching[0]
    console.print(f"  Found pending order via CCXT:")
    console.print(f"    id:     {order_info['id'][:26]}…")
    console.print(f"    type:   {order_info.get('type')}")
    console.print(f"    side:   {order_info.get('side')}")
    console.print(f"    price:  ${order_info.get('price', 0):,.2f}")
    console.print(f"    status: {order_info.get('status')}")

    # ------------------------------------------------------------------
    # Step 5: Cancel via CCXT cancel_order
    # ------------------------------------------------------------------
    console.print(f"\n[bold cyan]Step 5 — Cancel via CCXT cancel_order()[/bold cyan]")

    cancel_result = gmx.cancel_order(order_key_hex, symbol=MARKET_SYMBOL)

    assert cancel_result.get("status") == "cancelled", f"Expected status='cancelled', got {cancel_result.get('status')!r}"

    cancel_tx_hash = cancel_result["info"].get("tx_hash", "unknown")
    console.print(f"  Cancel TX: [yellow]{cancel_tx_hash}[/yellow]")
    console.print(f"  Status:    [green]{cancel_result['status']}[/green]")

    # ------------------------------------------------------------------
    # Step 6: Confirm via CCXT fetch_orders
    # ------------------------------------------------------------------
    console.print(f"\n[bold cyan]Step 6 — Confirm cancellation via CCXT fetch_orders()[/bold cyan]")

    pending_after = gmx.fetch_orders(symbol=MARKET_SYMBOL)
    still_there = [o for o in pending_after if o.get("id") == order_key_hex]

    if still_there:
        console.print("[red]Order still appears in fetch_orders() — cancellation may have failed.[/red]")
        sys.exit(1)

    console.print(f"  [green]Order {order_key_hex[:26]}… is no longer in fetch_orders().[/green]")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    console.print("\n" + "=" * 60)
    console.print("[bold green]CCXT limit order create → cancel lifecycle complete![/bold green]")
    console.print("=" * 60)
    console.print(f"\n  Create TX:  {tx_hash.hex()}")
    console.print(f"  Cancel TX:  {cancel_tx_hash}")
    console.print(f"  Order key:  {order_key_hex}")


if __name__ == "__main__":
    main()

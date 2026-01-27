#!/usr/bin/env python
"""Fetch GMX order status via DataStore and Subsquid GraphQL.

This script checks order status in two ways:
1. DataStore on-chain check - is the order still pending?
2. Subsquid GraphQL query - get execution details if order was executed/cancelled

Usage:
    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    export ORDER_KEY=0x1234...abcd
    export GMX_CHAIN=arbitrum  # or avalanche, arbitrum_sepolia
    poetry run python scripts/gmx/fetch_order_status.py

Environment variables:
    ORDER_KEY: The order key (32-byte hex string with 0x prefix)
    GMX_CHAIN: Chain name (default: arbitrum)
    JSON_RPC_ARBITRUM: RPC URL for Arbitrum (required for DataStore check)
    JSON_RPC_AVALANCHE: RPC URL for Avalanche (if using avalanche chain)
    TIMEOUT_SECONDS: Subsquid query timeout in seconds (default: 5)
"""

import json
import logging
import os
import sys
from datetime import datetime

from web3 import Web3

from eth_defi.gmx.contracts import get_datastore_contract
from eth_defi.gmx.graphql.client import GMXSubsquidClient
from eth_defi.gmx.order_tracking import ORDER_LIST_KEY
from eth_defi.provider.multi_provider import create_multi_provider_web3

logger = logging.getLogger(__name__)


def format_usd(value: str | None, decimals: int = 30) -> str:
    """Format a USD value from raw integer string."""
    if not value:
        return "N/A"
    try:
        val = int(value) / (10**decimals)
        return f"${val:,.2f}"
    except (ValueError, TypeError):
        return str(value)


def format_price(value: str | None, decimals: int = 30) -> str:
    """Format a price value from raw integer string.

    GMX prices use (30 - token_decimals) precision.
    For USDC (6 decimals): use 24 decimals total
    For ETH (18 decimals): use 12 decimals total
    """
    if not value:
        return "N/A"
    try:
        val_int = int(value)
        # Try different decimal interpretations to find reasonable price
        val_30 = val_int / (10**30)  # Flat 30 decimals
        val_24 = val_int / (10**24)  # 30 - 6 (USDC collateral)
        val_12 = val_int / (10**12)  # 30 - 18 (ETH)

        # Use the interpretation that gives a reasonable price (> $0.01, < $1M)
        if 0.01 < val_24 < 1000000:
            return f"${val_24:,.2f}"
        elif 0.01 < val_12 < 1000000:
            return f"${val_12:,.2f}"
        elif val_30 > 0:
            return f"${val_30:,.8f}"
        else:
            return f"Raw: {value}"
    except (ValueError, TypeError):
        return str(value)


def format_timestamp(ts: str | int | None) -> str:
    """Format a Unix timestamp to human-readable date."""
    if not ts:
        return "N/A"
    try:
        dt = datetime.utcfromtimestamp(int(ts))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return str(ts)


def get_rpc_env_var(chain: str) -> str:
    """Get the environment variable name for the RPC URL based on chain."""
    chain_map = {
        "arbitrum": "JSON_RPC_ARBITRUM",
        "avalanche": "JSON_RPC_AVALANCHE",
        "arbitrum_sepolia": "JSON_RPC_ARBITRUM_SEPOLIA",
    }
    return chain_map.get(chain.lower(), f"JSON_RPC_{chain.upper()}")


def check_datastore_pending(web3: Web3, order_key_bytes: bytes, chain: str) -> bool:
    """Check if order is still pending in the DataStore."""
    try:
        datastore = get_datastore_contract(web3, chain)
        is_pending = datastore.functions.containsBytes32(ORDER_LIST_KEY, order_key_bytes).call()
        return is_pending
    except Exception as e:
        logger.warning("Failed to query DataStore: %s", e)
        return None


def main():
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Get environment variables
    order_key = os.environ.get("ORDER_KEY")
    chain = os.environ.get("GMX_CHAIN", "arbitrum")
    timeout = int(os.environ.get("TIMEOUT_SECONDS", "5"))

    if not order_key:
        print("Error: ORDER_KEY environment variable is required")
        print("Usage: export ORDER_KEY=0x... && poetry run python scripts/gmx/fetch_order_status.py")
        sys.exit(1)

    # Ensure order_key has 0x prefix
    if not order_key.startswith("0x"):
        order_key = "0x" + order_key

    # Convert to bytes for DataStore check
    order_key_bytes = bytes.fromhex(order_key[2:])

    print("\n" + "=" * 60)
    print("GMX ORDER STATUS CHECK")
    print("=" * 60)
    print(f"  Chain: {chain}")
    print(f"  Order Key: {order_key}")
    print()

    # Get RPC URL
    rpc_env_var = get_rpc_env_var(chain)
    rpc_url = os.environ.get(rpc_env_var)

    # Step 1: Check DataStore if RPC is available
    web3 = None
    is_pending = None

    if rpc_url:
        print("STEP 1: Checking DataStore (on-chain)...")
        try:
            web3 = create_multi_provider_web3(rpc_url)
            is_pending = check_datastore_pending(web3, order_key_bytes, chain)

            if is_pending is True:
                print("  ⏳ Order is PENDING in DataStore")
                print("     The order exists and is waiting for keeper execution.")
            elif is_pending is False:
                print("  ✅ Order is NOT in DataStore (already executed or cancelled)")
            else:
                print("  ⚠️  Could not query DataStore")
        except Exception as e:
            print(f"  ⚠️  Error connecting to RPC: {e}")
    else:
        print(f"STEP 1: Skipped DataStore check ({rpc_env_var} not set)")

    print()

    # Step 2: Query Subsquid for execution details
    print(f"STEP 2: Querying Subsquid GraphQL (timeout: {timeout}s)...")

    client = GMXSubsquidClient(chain=chain)

    try:
        action = client.get_trade_action_by_order_key(
            order_key,
            timeout_seconds=timeout,
            poll_interval=0.5,
        )
    except Exception as e:
        print(f"  ⚠️  Error querying Subsquid: {e}")
        action = None

    if not action:
        print("  ❌ No trade action found in Subsquid")

        # Step 3: Fallback to EventEmitter log scanning if we have RPC access
        if web3 and is_pending is False:
            print()
            print("STEP 3: Falling back to EventEmitter log scan...")

            from eth_defi.gmx.contracts import get_contract_addresses
            from eth_defi.gmx.events import decode_gmx_event

            addresses = get_contract_addresses(chain)
            event_emitter = addresses.eventemitter
            current_block = web3.eth.block_number
            search_blocks = 10000  # Search last ~1 day of blocks

            from_block = max(0, current_block - search_blocks)

            try:
                logs = web3.eth.get_logs(
                    {
                        "address": event_emitter,
                        "fromBlock": from_block,
                        "toBlock": current_block,
                    }
                )

                print(f"  Scanning {len(logs)} logs from blocks {from_block}-{current_block}...")

                # Find OrderExecuted/OrderCancelled matching our order key
                for log in logs:
                    try:
                        event = decode_gmx_event(web3, log)
                        if not event:
                            continue

                        if event.event_name not in ("OrderExecuted", "OrderCancelled", "OrderFrozen"):
                            continue

                        # Check if this matches our order key
                        event_order_key = event.topic1 or event.get_bytes32("key")
                        if event_order_key != order_key_bytes:
                            continue

                        # Found it! Build action dict from event
                        tx_hash = log["transactionHash"].hex() if isinstance(log["transactionHash"], bytes) else log["transactionHash"]

                        action = {
                            "eventName": event.event_name,
                            "orderKey": order_key,
                            "orderType": event.get_uint("orderType"),
                            "isLong": event.get_bool("isLong"),
                            "timestamp": None,  # Get from transaction
                            "transaction": {"hash": tx_hash},
                        }

                        # Get execution details if OrderExecuted
                        if event.event_name == "OrderExecuted":
                            # Need to find PositionIncrease/PositionDecrease event in same tx for prices
                            receipt = web3.eth.get_transaction_receipt(tx_hash)
                            for receipt_log in receipt["logs"]:
                                try:
                                    pos_event = decode_gmx_event(web3, receipt_log)
                                    if pos_event and pos_event.event_name in ("PositionIncrease", "PositionDecrease"):
                                        action["executionPrice"] = str(pos_event.get_uint("executionPrice") or 0)
                                        action["sizeDeltaUsd"] = str(pos_event.get_uint("sizeDeltaUsd") or 0)
                                        action["priceImpactUsd"] = str(pos_event.get_int("priceImpactUsd") or 0)
                                        action["acceptablePrice"] = str(event.get_uint("acceptablePrice") or 0)
                                        action["triggerPrice"] = str(event.get_uint("triggerPrice") or 0)
                                        action["positionFeeAmount"] = str(pos_event.get_uint("positionFeeAmount") or 0)
                                        action["borrowingFeeAmount"] = str(pos_event.get_uint("borrowingFeeAmount") or 0)
                                        action["fundingFeeAmount"] = str(pos_event.get_uint("fundingFeeAmount") or 0)

                                        # Get pending price impact fields
                                        action["pendingPriceImpactUsd"] = str(pos_event.get_int("pendingPriceImpactUsd") or 0)
                                        # Note: pendingPriceImpactAmount not in event, only in raw tuple
                                        break
                                except Exception:
                                    continue

                        # Get cancellation reason if OrderCancelled
                        elif event.event_name == "OrderCancelled":
                            action["reason"] = event.get_string("reason") or ""
                            action["reasonBytes"] = event.get_bytes32("reasonBytes").hex() if event.get_bytes32("reasonBytes") else ""

                        print(f"  ✅ Found {event.event_name} event in tx {tx_hash[:18]}...")
                        break

                    except Exception as e:
                        continue

            except Exception as e:
                print(f"  ⚠️  Error scanning logs: {e}")

        if not action:
            # Provide diagnosis
            print()
            print("=" * 60)
            print("DIAGNOSIS")
            print("=" * 60)

            if is_pending is True:
                print("\n  📋 Status: ORDER IS PENDING")
                print("     The order was created but keepers haven't executed it yet.")
                print("     This is normal - GMX keepers typically execute within seconds.")
                print("\n  Possible reasons for delay:")
                print("     - Network congestion")
                print("     - Price moved outside acceptable range")
                print("     - Insufficient execution fee")
                print("\n  Next steps:")
                print("     - Wait a few more seconds and check again")
                print("     - Check the transaction on Arbiscan for details")
            elif is_pending is False:
                print("\n  ⚠️  Status: EXECUTED BUT NOT INDEXED")
                print("     Order was removed from DataStore but Subsquid doesn't have it yet.")
                print("     The indexer may be behind. Try again in a few seconds.")
            else:
                print("\n  ❓ Status: UNKNOWN")
                print("     Could not determine order status.")
                print(f"     Set {rpc_env_var} to enable DataStore check.")

        sys.exit(0)

    # Display execution results
    print()
    print("=" * 60)
    print("EXECUTION DETAILS")
    print("=" * 60)

    event_name = action.get("eventName", "unknown")
    status_emoji = {
        "OrderExecuted": "✅",
        "OrderCancelled": "❌",
        "OrderFrozen": "❄️",
        "OrderCreated": "⏳",
    }.get(event_name, "❔")

    print(f"\n{status_emoji} Event: {event_name}")
    print(f"   Order Key: {action.get('orderKey', 'N/A')}")
    print(f"   Order Type: {action.get('orderType', 'N/A')}")
    print(f"   Is Long: {action.get('isLong', 'N/A')}")
    print(f"   Timestamp: {format_timestamp(action.get('timestamp'))}")

    print("\nPRICE INFO:")
    print(f"   Execution Price: {format_price(action.get('executionPrice'))}")
    print(f"   Acceptable Price: {format_price(action.get('acceptablePrice'))}")
    print(f"   Trigger Price: {format_price(action.get('triggerPrice'))}")

    print("\nPOSITION INFO:")
    print(f"   Size Delta USD: {format_usd(action.get('sizeDeltaUsd'))}")
    print(f"   PnL USD: {format_usd(action.get('pnlUsd'))}")
    print(f"   Price Impact USD: {format_usd(action.get('priceImpactUsd'))}")
    if action.get("pendingPriceImpactUsd"):
        print(f"   Pending Price Impact USD: {format_usd(action.get('pendingPriceImpactUsd'))}")

    print("\nFEES:")
    print(f"   Position Fee: {format_usd(action.get('positionFeeAmount'))}")
    print(f"   Borrowing Fee: {format_usd(action.get('borrowingFeeAmount'))}")
    print(f"   Funding Fee: {format_usd(action.get('fundingFeeAmount'))}")

    tx = action.get("transaction", {})
    if tx:
        print("\nTRANSACTION:")
        print(f"   Hash: {tx.get('hash', 'N/A')}")
        print(f"   Time: {format_timestamp(tx.get('timestamp'))}")

    if event_name == "OrderCancelled":
        print("\nCANCELLATION REASON:")
        print(f"   Reason: {action.get('reason', 'N/A')}")
        reason_bytes = action.get("reasonBytes")
        if reason_bytes:
            print(f"   Reason Bytes: {reason_bytes}")

    print("\n" + "=" * 60)
    print("RAW RESPONSE:")
    print("=" * 60)
    print(json.dumps(action, indent=2, default=str))


if __name__ == "__main__":
    main()

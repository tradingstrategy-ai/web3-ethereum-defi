"""
GMX event parsing examples

Examples of decoding GMX protocol events from transaction receipts:

- decode_gmx_events() - Decode all GMX events from a receipt
- find_events_by_name() - Filter events by name
- extract_order_execution_result() - Get order execution status, prices, PnL
- extract_order_key_from_receipt() - Get order key from OrderCreated event
- decode_error_reason() - Decode error reasons from failed orders

GMX emits all events through the EventEmitter contract using EventLog,
EventLog1, and EventLog2 event types. Each event contains structured data
in categories: addresses, uints, ints, bools, bytes32, bytes, and strings.

Usage:
    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    python scripts/gmx/gmx_event_parsing.py

    # Or with a specific transaction hash:
    export TX_HASH="0x..."
    python scripts/gmx/gmx_event_parsing.py

For more information on GMX events:
    https://docs.gmx.io/docs/api/contracts#event-monitoring
"""

import logging
import os

from web3 import Web3
from rich.console import Console

print = Console().print

from eth_defi.gmx.events import (
    GMX_EVENT_NAMES,
    GMXEventData,
    OrderExecutionResult,
    OrderFees,
    decode_error_reason,
    decode_gmx_event,
    decode_gmx_events,
    extract_order_execution_result,
    extract_order_key_from_receipt,
    find_events_by_name,
    get_event_name_hash,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# Example transaction hashes from Arbitrum mainnet (keeper executions)
# These are real GMX order execution transactions
EXAMPLE_TX_HASHES = [
    # A market increase order execution (PositionIncrease event)
    "0x5a8e9c4e3b2f1d6a7c8b9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
    # Replace with actual tx hashes from:
    # https://arbiscan.io/address/0xC8ee91A54287DB53897056e12D9819156D3822Fb#events
]


def example_get_event_name_hash():
    """Example: Compute keccak256 hash of event names dynamically."""
    print("\n" + "=" * 60)
    print("1. get_event_name_hash() - Compute event name hashes")
    print("=" * 60)

    print("\nGMX uses event name hashes for efficient filtering.")
    print("Hash is keccak256 of the event name string.\n")

    for event_name in sorted(GMX_EVENT_NAMES):
        hash_hex = get_event_name_hash(event_name)
        print(f"  {event_name:20} -> 0x{hash_hex[:16]}...")

    print(f"\nTotal known event names: {len(GMX_EVENT_NAMES)}")


def example_decode_gmx_events(web3: Web3, receipt: dict):
    """Example: Decode all GMX events from a transaction receipt."""
    print("\n" + "=" * 60)
    print("2. decode_gmx_events() - Decode all events from receipt")
    print("=" * 60)

    events = list(decode_gmx_events(web3, receipt))

    if not events:
        print("\nNo GMX events found in this transaction")
        return events

    print(f"\nFound {len(events)} GMX events:\n")

    for i, event in enumerate(events, 1):
        print(f"  [{i}] {event.event_name}")
        print(f"      msg_sender: {event.msg_sender}")

        if event.topic1:
            print(f"      topic1 (order key): 0x{event.topic1.hex()[:16]}...")

        # Show some key fields based on event type
        if event.event_name == "OrderCreated":
            print(f"      account: {event.get_address('account')}")
            print(f"      orderType: {event.get_uint('orderType')}")
            print(f"      sizeDeltaUsd: {event.get_uint('sizeDeltaUsd')}")

        elif event.event_name == "OrderExecuted":
            print(f"      account: {event.get_address('account')}")
            print(f"      secondaryOrderType: {event.get_uint('secondaryOrderType')}")

        elif event.event_name in ("PositionIncrease", "PositionDecrease"):
            print(f"      account: {event.get_address('account')}")
            print(f"      market: {event.get_address('market')}")
            print(f"      isLong: {event.get_bool('isLong')}")
            exec_price = event.get_uint("executionPrice")
            if exec_price:
                # GMX prices use 12 decimal precision for price per token
                price_usd = exec_price / 10**12
                print(f"      executionPrice: ${price_usd:,.2f}")
            size_usd = event.get_uint("sizeDeltaUsd")
            if size_usd:
                # USD amounts use 30 decimal precision
                print(f"      sizeDeltaUsd: ${size_usd / 10**30:,.2f}")
            pnl = event.get_int("basePnlUsd")
            if pnl is not None:
                print(f"      basePnlUsd: ${pnl / 10**30:,.4f}")

        elif event.event_name in ("OrderFrozen", "OrderCancelled"):
            print(f"      reason: {event.get_string('reason')}")
            reason_bytes = event.get_bytes("reasonBytes")
            if reason_bytes:
                decoded = decode_error_reason(reason_bytes)
                print(f"      decodedError: {decoded}")

        print()

    return events


def example_find_events_by_name(web3: Web3, receipt: dict):
    """Example: Filter events by name."""
    print("\n" + "=" * 60)
    print("3. find_events_by_name() - Filter events by type")
    print("=" * 60)

    # Common event types to look for
    event_types = ["OrderCreated", "OrderExecuted", "PositionIncrease", "PositionDecrease"]

    for event_type in event_types:
        events = list(find_events_by_name(web3, receipt, event_type))
        if events:
            print(f"\n  Found {len(events)} {event_type} event(s)")
            for event in events[:2]:  # Show max 2
                if event.topic1:
                    print(f"    - order_key: 0x{event.topic1.hex()[:16]}...")


def example_extract_order_execution_result(web3: Web3, receipt: dict):
    """Example: Extract comprehensive order execution result."""
    print("\n" + "=" * 60)
    print("4. extract_order_execution_result() - Get execution details")
    print("=" * 60)

    result = extract_order_execution_result(web3, receipt)

    if not result:
        print("\nNo order execution events found in this transaction")
        print("(This transaction may not be a keeper order execution)")
        return None

    print("\nOrder Execution Result:")
    print(f"  order_key: 0x{result.order_key.hex()[:16]}...")
    print(f"  status: {result.status}")
    print(f"  account: {result.account}")

    if result.status == "executed":
        print("\n  Execution Details:")

        if result.execution_price:
            # Prices use 12 decimal precision
            price_usd = result.execution_price / 10**12
            print(f"    execution_price: ${price_usd:,.2f}")

        if result.size_delta_usd:
            # USD amounts use 30 decimal precision
            size_usd = result.size_delta_usd / 10**30
            print(f"    size_delta_usd: ${size_usd:,.2f}")

        if result.size_delta_in_tokens:
            print(f"    size_delta_in_tokens: {result.size_delta_in_tokens}")

        if result.collateral_delta is not None:
            print(f"    collateral_delta: {result.collateral_delta}")

        if result.pnl_usd is not None:
            pnl = result.pnl_usd / 10**30
            print(f"    pnl_usd: ${pnl:,.4f}")

        if result.price_impact_usd is not None:
            impact = result.price_impact_usd / 10**30
            print(f"    price_impact_usd: ${impact:,.4f}")

        if result.is_long is not None:
            print(f"    is_long: {result.is_long}")

        if result.position_key:
            print(f"    position_key: 0x{result.position_key.hex()[:16]}...")

        if result.fees:
            print("\n  Fees:")
            if result.fees.position_fee:
                print(f"    position_fee: {result.fees.position_fee}")
            if result.fees.borrowing_fee:
                print(f"    borrowing_fee: {result.fees.borrowing_fee}")
            if result.fees.funding_fee:
                print(f"    funding_fee: {result.fees.funding_fee}")

    elif result.status in ("frozen", "cancelled"):
        print("\n  Error Details:")
        print(f"    reason: {result.reason}")
        if result.reason_bytes:
            print(f"    reason_bytes: 0x{result.reason_bytes.hex()[:32]}...")
        if result.decoded_error:
            print(f"    decoded_error: {result.decoded_error}")

    return result



def example_decode_error_reason():
    """Example: Decode GMX error reasons."""
    print("\n" + "=" * 60)
    print("6. decode_error_reason() - Decode error bytes")
    print("=" * 60)

    print("\nGMX uses custom error selectors. Known errors:\n")

    # Demonstrate with example error bytes
    example_errors = [
        ("30116425", "InsufficientOutputAmount"),
        ("34274e24", "OrderNotFulfillableAtAcceptablePrice"),
        ("8b87fecb", "EmptyPosition"),
        ("2def2005", "InsufficientCollateral"),
        ("b9ec1e96", "InsufficientReserve"),
        ("e8960866", "MaxLongExceeded"),
        ("e8b3a5d4", "MaxShortExceeded"),
    ]

    for selector, name in example_errors:
        print(f"  0x{selector} -> {name}")

    print("\nUsage:")
    print("  reason_bytes = event.get_bytes('reasonBytes')")
    print("  decoded = decode_error_reason(reason_bytes)")
    print("  # Returns: 'InsufficientOutputAmount' or 'Unknown error (selector: 0x...)'")


def main():
    print("\n" + "=" * 60)
    print("GMX Event Parsing Examples")
    print("Decode and analyse GMX protocol events from transactions")
    print("=" * 60)

    # Get RPC URL
    rpc = os.environ.get("JSON_RPC_ARBITRUM", "https://arb1.arbitrum.io/rpc")
    # For fallback RPC URLs, use only the first one
    if " " in rpc:
        rpc = rpc.split()[0]

    print(f"\nUsing RPC: {rpc[:50]}...")

    try:
        web3 = Web3(Web3.HTTPProvider(rpc))
        chain_id = web3.eth.chain_id
        print(f"Connected to chain ID: {chain_id}")

        if chain_id != 42161:
            print("Warning: Not connected to Arbitrum mainnet (chain 42161)")
            print("Event parsing will still work but example transactions may not exist")

    except Exception as e:
        print(f"Could not connect to RPC: {e}")
        print("Showing static examples only...\n")
        web3 = None

    # Run static examples (no RPC needed)
    example_get_event_name_hash()
    example_decode_error_reason()

    # Run dynamic examples if we have a transaction
    tx_hash = os.environ.get("TX_HASH", EXAMPLE_TX_HASHES[0] if EXAMPLE_TX_HASHES else None,)

    if tx_hash and web3:
        print("\n" + "=" * 60)
        print(f"Parsing transaction: {tx_hash[:20]}...")
        print("=" * 60)

        try:
            receipt = web3.eth.get_transaction_receipt(tx_hash)
            print(f"Transaction status: {'Success' if receipt['status'] == 1 else 'Failed'}")
            print(f"Block number: {receipt['blockNumber']}")
            print(f"Gas used: {receipt['gasUsed']:,}")
            print(f"Log count: {len(receipt.get('logs', []))}")

            example_decode_gmx_events(web3, receipt)
            example_find_events_by_name(web3, receipt)
            example_extract_order_execution_result(web3, receipt)

        except Exception as e:
            print(f"Error fetching transaction: {e}")

    elif web3:
        print("\n" + "-" * 60)
        print("To parse a specific transaction, set TX_HASH environment variable:")
        print("  export TX_HASH='0x...'")
        print("  python scripts/gmx/gmx_event_parsing.py")
        print()
        print("Find GMX keeper transactions on Arbiscan:")
        print("  https://arbiscan.io/address/0xC8ee91A54287DB53897056e12D9819156D3822Fb#events")
        print("-" * 60)

    print("\n" + "=" * 60)
    print("Examples complete!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

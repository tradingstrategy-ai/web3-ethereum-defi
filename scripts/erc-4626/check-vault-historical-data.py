"""Check historical data for a single vault.

Performs historical reads for a single vault and displays results in a table.

Usage:

.. code-block:: shell

    # Check Valos vault on Ethereum
    VAULT_ID="1-0xF0A33207A6e363faa58Aed86Abb7b4d2E51591c0" \\
      JSON_RPC_URL=$JSON_RPC_ETHEREUM \\
      poetry run python scripts/erc-4626/check-vault-historical-data.py

    # Limit to 10 rows from head + tail
    VAULT_ID="1-0xF0A33207A6e363faa58Aed86Abb7b4d2E51591c0" \\
      LIMIT=10 \\
      poetry run python scripts/erc-4626/check-vault-historical-data.py

    # Scan more blocks
    VAULT_ID="1-0xF0A33207A6e363faa58Aed86Abb7b4d2E51591c0" \\
      BLOCK_COUNT=100 \\
      poetry run python scripts/erc-4626/check-vault-historical-data.py

Example output::

    Connected to chain 1: Ethereum
    Last block is: 24,147,061
    Vault: RWA Backed Lending by Valos
    Protocol: Accountable
    Scanning 50 blocks from 24,147,011 to 24,147,061

    Historical data (showing 20 head + 20 tail rows):
    ╒══════════════════════════╤════════════════╤═══════════════╤═══════════════╤═══════════════════════╤═══════════════╕
    │ timestamp                │ block_number   │ share_price   │ total_assets  │ available_liquidity   │ utilisation   │
    ╞══════════════════════════╪════════════════╪═══════════════╪═══════════════╪═══════════════════════╪═══════════════╡
    │ 2025-01-15 10:30:00      │ 24,147,011     │ 1.05          │ 100,000,000   │ 5,000,000             │ 95.0%         │
    ...

"""

import datetime
import logging
import os
from decimal import Decimal

import pandas as pd
from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.timestamp import get_block_timestamp
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec, VaultHistoricalRead

logger = logging.getLogger(__name__)

setup_console_logging(default_log_level="INFO")


def format_number(value: Decimal | float | None, precision: int = 2) -> str:
    """Format a number for display."""
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        value = float(value)
    if abs(value) < 1:
        return f"{value:.{precision}f}"
    return f"{value:,.{precision}f}"


def format_percent(value: float | None) -> str:
    """Format a percentage for display."""
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def main():
    # Read environment variables
    vault_id = os.environ.get("VAULT_ID")
    assert vault_id is not None, "Set VAULT_ID environment variable (e.g., '1-0xF0A33207A6e363faa58Aed86Abb7b4d2E51591c0')"

    limit = int(os.environ.get("LIMIT", "20"))
    block_count = int(os.environ.get("BLOCK_COUNT", "50"))

    # Parse vault spec
    spec = VaultSpec.parse_string(vault_id)

    # Connect to chain
    json_rpc_url = read_json_rpc_url(spec.chain_id)
    web3 = create_multi_provider_web3(json_rpc_url)
    name = get_chain_name(web3.eth.chain_id)

    print(f"Connected to chain {web3.eth.chain_id}: {name}")
    print(f"Last block is: {web3.eth.block_number:,}")

    assert web3.eth.chain_id == spec.chain_id, f"Chain ID mismatch: {web3.eth.chain_id} != {spec.chain_id}"

    # Detect vault features and create instance
    features = detect_vault_features(web3, spec.vault_address)
    vault = create_vault_instance(web3, spec.vault_address, features)

    print(f"Vault: {vault.name}")
    print(f"Protocol: {vault.get_protocol_name()}")
    print(f"Features: {[f.name for f in features]}")
    print(f"Denomination: {vault.denomination_token.symbol if vault.denomination_token else 'Unknown'}")

    # Get historical reader
    reader = vault.get_historical_reader(stateful=False)

    # Determine block range
    end_block = web3.eth.block_number
    start_block = max(vault.first_seen_at_block or 1, end_block - block_count)

    print(f"\nScanning {end_block - start_block} blocks from {start_block:,} to {end_block:,}")

    # Collect historical data
    historical_reads: list[VaultHistoricalRead] = []

    # We'll sample evenly across the block range
    sample_blocks = []
    step = max(1, (end_block - start_block) // min(block_count, end_block - start_block))
    current_block = start_block
    while current_block <= end_block:
        sample_blocks.append(current_block)
        current_block += step

    # Ensure we include the end block
    if sample_blocks[-1] != end_block:
        sample_blocks.append(end_block)

    print(f"Sampling {len(sample_blocks)} blocks")

    # Read historical data for each sample block
    for block_number in sample_blocks:
        try:
            # Get block timestamp
            timestamp = get_block_timestamp(web3, block_number)

            # Construct multicalls
            calls = list(reader.construct_multicalls())

            # Execute multicalls
            from eth_defi.event_reader.multicall_batcher import read_multicall_single_block

            call_results = read_multicall_single_block(web3, block_number, calls)

            # Process result
            read = reader.process_result(block_number, timestamp, call_results)
            historical_reads.append(read)

        except Exception as e:
            logger.warning(f"Error reading block {block_number}: {e}")
            continue

    if not historical_reads:
        print("No historical data could be read")
        return

    print(f"\nSuccessfully read {len(historical_reads)} data points")

    # Convert to dataframe
    rows = []
    for read in historical_reads:
        row = {
            "timestamp": read.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "block_number": f"{read.block_number:,}",
            "share_price": format_number(read.share_price, 6),
            "total_assets": format_number(read.total_assets, 0),
            "total_supply": format_number(read.total_supply, 0),
        }

        # Add optional fields if present
        if read.available_liquidity is not None:
            row["available_liquidity"] = format_number(read.available_liquidity, 0)
        if read.utilisation is not None:
            row["utilisation"] = format_percent(read.utilisation)
        if read.max_deposit is not None:
            row["max_deposit"] = format_number(read.max_deposit, 0)
        if read.errors:
            row["errors"] = read.errors

        rows.append(row)

    df = pd.DataFrame(rows)

    # Display head + tail
    if len(df) <= limit * 2:
        # Show all rows if we have fewer than limit * 2
        display_df = df
        print(f"\nHistorical data (all {len(df)} rows):")
    else:
        # Show head + tail
        head_df = df.head(limit)
        tail_df = df.tail(limit)
        display_df = pd.concat([head_df, tail_df])
        print(f"\nHistorical data (showing {limit} head + {limit} tail rows out of {len(df)} total):")

    print(tabulate(display_df, headers="keys", tablefmt="grid", showindex=False))

    # Summary statistics
    print(f"\nSummary:")
    print(f"  First timestamp: {historical_reads[0].timestamp}")
    print(f"  Last timestamp: {historical_reads[-1].timestamp}")
    print(f"  Duration: {(historical_reads[-1].timestamp - historical_reads[0].timestamp).days} days")

    # Show current values if available
    if historical_reads[-1].total_assets is not None:
        print(f"  Latest total assets: ${historical_reads[-1].total_assets:,.0f}")
    if historical_reads[-1].available_liquidity is not None:
        print(f"  Latest available liquidity: ${historical_reads[-1].available_liquidity:,.0f}")
    if historical_reads[-1].utilisation is not None:
        print(f"  Latest utilisation: {historical_reads[-1].utilisation * 100:.1f}%")
    if historical_reads[-1].share_price is not None:
        print(f"  Latest share price: {historical_reads[-1].share_price:.6f}")

    print("\nAll OK")


if __name__ == "__main__":
    main()

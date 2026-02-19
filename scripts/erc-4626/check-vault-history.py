"""Check historical data for a single vault using the scan-prices pipeline.

Uses the same ``VaultHistoricalReadMulticaller`` as ``scan-prices.py``
to ensure results match what the production pipeline produces.

Environment variables:

- ``VAULT_ID``: Required. Vault identifier in format ``chain_id-address``.
- ``START_BLOCK``: Optional. First block to read. Defaults to 1000 blocks before end.
- ``END_BLOCK``: Optional. Last block to read. Defaults to latest block.
- ``STEP``: Optional. Block step between reads. Defaults to chain block time based 1h step.
- ``LIMIT``: Optional. Number of rows to show in head + tail. Defaults to 10.
- ``MAX_WORKERS``: Optional. Number of parallel workers. Defaults to 4.

Usage:

.. code-block:: shell

    # Check Valos vault on Monad
    VAULT_ID="143-0x8d3f9f9eb2f5e8b48efbb4074440d1e2a34bc365" \\
      poetry run python scripts/erc-4626/check-vault-history.py

    # Show fewer rows, scan more blocks
    VAULT_ID="143-0x8d3f9f9eb2f5e8b48efbb4074440d1e2a34bc365" \\
      START_BLOCK=54000000 \\
      LIMIT=5 \\
      poetry run python scripts/erc-4626/check-vault-history.py

"""

import datetime
import logging
import os
from decimal import Decimal

import pandas as pd
from tabulate import tabulate

from eth_defi.chain import EVM_BLOCK_TIMES, get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.event_reader.multicall_batcher import read_multicall_historical
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.historical import VaultHistoricalReadMulticaller

logger = logging.getLogger(__name__)


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
    vault_id = os.environ.get("VAULT_ID")
    assert vault_id is not None, "Set VAULT_ID environment variable (e.g., '143-0x8d3f9f9eb2f5e8b48efbb4074440d1e2a34bc365')"

    limit = int(os.environ.get("LIMIT", "10"))
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))

    # Parse vault spec and connect
    spec = VaultSpec.parse_string(vault_id)
    json_rpc_url = read_json_rpc_url(spec.chain_id)
    web3 = create_multi_provider_web3(json_rpc_url)
    web3factory = MultiProviderWeb3Factory(json_rpc_url)
    chain_name = get_chain_name(web3.eth.chain_id)

    assert web3.eth.chain_id == spec.chain_id, f"Chain ID mismatch: {web3.eth.chain_id} != {spec.chain_id}"

    print(f"Connected to chain {web3.eth.chain_id}: {chain_name}")
    print(f"Last block is: {web3.eth.block_number:,}")

    # Detect vault features and create instance
    token_cache = TokenDiskCache()
    features = detect_vault_features(web3, spec.vault_address)
    vault = create_vault_instance(web3, spec.vault_address, features, token_cache=token_cache)

    print(f"Vault: {vault.name}")
    print(f"Protocol: {vault.get_protocol_name()}")
    print(f"Features: {[f.name for f in features]}")
    print(f"Denomination: {vault.denomination_token.symbol if vault.denomination_token else 'Unknown'}")

    # Determine block range and step
    end_block = int(os.environ.get("END_BLOCK", str(web3.eth.block_number)))
    block_time = EVM_BLOCK_TIMES.get(spec.chain_id)
    assert block_time is not None, f"Block time not configured for chain: {spec.chain_id}"

    step = int(os.environ.get("STEP", "0"))
    if step == 0:
        # Default to ~1h steps
        step = int(datetime.timedelta(hours=1) // datetime.timedelta(seconds=block_time))

    start_block = int(os.environ.get("START_BLOCK", str(max(1, end_block - step * 100))))
    vault.first_seen_at_block = start_block

    print(f"\nScanning blocks {start_block:,} to {end_block:,}, step {step:,} blocks (~{step * block_time / 3600:.1f}h)")

    # Use the same pipeline as scan-prices.py
    multicaller = VaultHistoricalReadMulticaller(
        web3factory=web3factory,
        supported_quote_tokens=None,
        max_workers=max_workers,
        token_cache=token_cache,
    )

    historical_reads: list[VaultHistoricalRead] = []
    for read in multicaller.read_historical(
        vaults=[vault],
        start_block=start_block,
        end_block=end_block,
        step=step,
        reader_func=read_multicall_historical,
    ):
        historical_reads.append(read)

    if not historical_reads:
        print("No historical data could be read")
        return

    # Sort by block number
    historical_reads.sort(key=lambda r: r.block_number)

    print(f"\nSuccessfully read {len(historical_reads)} data points")

    # Build dataframe
    rows = []
    for read in historical_reads:
        row = {
            "timestamp": read.timestamp.strftime("%Y-%m-%d %H:%M"),
            "block": f"{read.block_number:,}",
            "share_price": format_number(read.share_price, 6),
            "total_assets": format_number(read.total_assets, 0),
            "total_supply": format_number(read.total_supply, 0),
        }

        if read.available_liquidity is not None:
            row["avail_liq"] = format_number(read.available_liquidity, 0)
        if read.utilisation is not None:
            row["util"] = format_percent(read.utilisation)
        if read.errors:
            row["errors"] = str(read.errors)[:40]

        rows.append(row)

    df = pd.DataFrame(rows)

    # Display head + tail
    if len(df) <= limit * 2:
        display_df = df
        print(f"\nHistorical data (all {len(df)} rows):")
    else:
        display_df = pd.concat([df.head(limit), df.tail(limit)])
        print(f"\nHistorical data ({limit} head + {limit} tail of {len(df)} total):")

    print(tabulate(display_df, headers="keys", tablefmt="grid", showindex=False))

    # Summary
    first = historical_reads[0]
    last = historical_reads[-1]
    duration = last.timestamp - first.timestamp

    print(f"\nSummary:")
    print(f"  Period: {first.timestamp.strftime('%Y-%m-%d')} to {last.timestamp.strftime('%Y-%m-%d')} ({duration.days} days)")

    if last.total_assets is not None:
        print(f"  Latest total assets: ${format_number(last.total_assets, 0)}")
    if last.available_liquidity is not None:
        print(f"  Latest available liquidity: ${format_number(last.available_liquidity, 0)}")
    if last.utilisation is not None:
        print(f"  Latest utilisation: {format_percent(last.utilisation)}")
    if last.share_price is not None:
        print(f"  Latest share price: {format_number(last.share_price, 6)}")

    print("\nAll OK")


if __name__ == "__main__":
    main()

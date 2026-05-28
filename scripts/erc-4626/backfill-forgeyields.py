"""Backfill ForgeYields historical TVL from their offchain API.

ForgeYields vaults have ``total_assets=NaN`` in the price parquet because
the on-chain TokenGateway only holds a residual. The ForgeYields API
provides ~30 days of daily TVL snapshots via ``historyReports``.

This script reads the existing parquet, matches ForgeYields rows by
nearest timestamp to the API history, and fills ``total_assets`` from the
API's denomination-token TVL.

Usage::

    poetry run python scripts/erc-4626/backfill-forgeyields.py

Environment variables:

- ``PARQUET_PATH`` — path to vault-prices-1h.parquet
  (default: ``~/.tradingstrategy/vaults/vault-prices-1h.parquet``)
- ``DRY_RUN`` — set to ``1`` to print what would be written without modifying the file
- ``LOG_LEVEL`` — logging level (default: ``info``)
"""

import logging
import math
import os
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from eth_defi.erc_4626.vault_protocol.forgeyields.offchain_metadata import fetch_forgeyields_history
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultHistoricalRead, verify_parquet_file

logger = logging.getLogger(__name__)

#: Ethereum chain id
ETHEREUM_CHAIN_ID = 1

#: Maximum time difference to consider a history entry a match for a parquet row
MAX_MATCH_DELTA_SECONDS = 24 * 3600  # 24 hours (API has daily snapshots)


def main():
    log_level = os.environ.get("LOG_LEVEL", "info").lower()
    setup_console_logging(default_log_level=log_level)

    parquet_path = Path(
        os.environ.get(
            "PARQUET_PATH",
            os.path.expanduser("~/.tradingstrategy/vaults/vault-prices-1h.parquet"),
        )
    )
    dry_run = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")

    if not parquet_path.exists():
        logger.error("Parquet file not found: %s", parquet_path)
        raise SystemExit(1)

    logger.info("Reading parquet from %s", parquet_path)
    table = pq.read_table(str(parquet_path))
    table = VaultHistoricalRead.migrate_parquet_schema(table)
    logger.info("Parquet has %d rows, %d columns", len(table), len(table.schema))

    # Fetch history from the API
    strategies = fetch_forgeyields_history()

    # Build lookup: gateway address (lowercase) -> list of (epoch_ts, tvl_denom_token)
    history_by_addr: dict[str, list[tuple[float, float]]] = {}
    for strat in strategies:
        if strat["ethereum_gateway"] is None:
            continue
        addr = strat["ethereum_gateway"].lower()
        entries = []
        for h in strat["history"]:
            epoch = h["timestamp"].timestamp()
            entries.append((epoch, h["tvl"]))
        entries.sort()
        history_by_addr[addr] = entries
        logger.info(
            "%s (%s): %d history entries, TVL range %.2f–%.2f %s",
            strat["name"],
            addr,
            len(entries),
            min(e[1] for e in entries) if entries else 0,
            max(e[1] for e in entries) if entries else 0,
            strat["underlying_symbol"],
        )

    if not history_by_addr:
        logger.warning("No ForgeYields strategies with Ethereum gateways found")
        return

    # Scan parquet rows and fill total_assets from history
    chains = table.column("chain").to_pylist()
    addresses = table.column("address").to_pylist()
    timestamps = table.column("timestamp").to_pylist()
    total_assets = table.column("total_assets").to_pylist()

    filled = 0
    matched_vaults = set()
    for i in range(len(table)):
        if chains[i] != ETHEREUM_CHAIN_ID:
            continue

        addr = addresses[i]
        if addr not in history_by_addr:
            continue

        row_ts = timestamps[i].timestamp() if hasattr(timestamps[i], "timestamp") else float(timestamps[i])
        history = history_by_addr[addr]

        # Find nearest history entry by timestamp
        best_delta = float("inf")
        best_tvl = None
        for epoch, tvl in history:
            delta = abs(row_ts - epoch)
            if delta < best_delta:
                best_delta = delta
                best_tvl = tvl

        if best_tvl is not None and best_delta <= MAX_MATCH_DELTA_SECONDS:
            total_assets[i] = best_tvl
            filled += 1
            matched_vaults.add(addr)
        else:
            # Row is outside the API history window. If it has a value, it is
            # a stale gateway residual from the old buggy on-chain reader.
            # Clear it to NaN so it does not pollute the historical series.
            if not math.isnan(total_assets[i]):
                total_assets[i] = float("nan")
                filled += 1
                matched_vaults.add(addr)

    logger.info(
        "Filled %d rows across %d vaults (of %d total rows)",
        filled,
        len(matched_vaults),
        len(table),
    )

    if filled == 0:
        logger.info("Nothing to backfill")
        return

    if dry_run:
        logger.info("DRY_RUN=1, not writing changes")
        return

    # Write back
    col_idx = table.schema.get_field_index("total_assets")
    table = table.set_column(col_idx, "total_assets", pa.array(total_assets, type=pa.float64()))

    temp_fd, temp_path = tempfile.mkstemp(suffix=".parquet", dir=str(parquet_path.parent))
    os.close(temp_fd)
    try:
        pq.write_table(table, temp_path, compression="zstd")
        verify_parquet_file(Path(temp_path), expected_rows=len(table), expected_schema=table.schema)
        os.replace(temp_path, str(parquet_path))
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    logger.info("Wrote backfilled parquet to %s", parquet_path)


if __name__ == "__main__":
    main()

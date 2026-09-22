"""Benchmark bounded sparkline preparation and rendering without R2 access.

The benchmark reads the production-shaped crypto daily Parquet, selects a
deterministic sample of eligible vaults, renders both output formats and
reports uncompressed/compressed payload sizes. It never creates an R2 client
or writes pipeline state.
"""

from __future__ import annotations

import gzip
import os
import time
from pathlib import Path

from tabulate import tabulate

from eth_defi.research.sparkline_export import (
    get_included_vault_ids,
    load_sparkline_price_data,
    prepare_vault_sparklines,
    render_sparklines,
)
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir


def main() -> None:  # noqa: PLR0914
    """Run the no-upload benchmark against the configured pipeline files."""
    setup_console_logging(only_log_file=False)
    data_dir = get_pipeline_data_dir()
    prices_path = Path(os.environ.get("SPARKLINE_PRICE_PATH", data_dir / "crypto-vaults" / "crypto-cleaned-vault-prices-1d.parquet"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", data_dir / "vault-metadata-db.pickle"))
    sample_size = int(os.environ.get("SPARKLINE_BENCHMARK_SAMPLE_SIZE", "100"))
    if sample_size < 1:
        message = "SPARKLINE_BENCHMARK_SAMPLE_SIZE must be positive"
        raise ValueError(message)

    started = time.perf_counter()
    prices_df = load_sparkline_price_data(prices_path)
    vault_db = VaultDatabase.read(vault_db_path)
    included_ids = get_included_vault_ids(vault_db, prices_df)
    selected_ids = set(sorted(included_ids)[:sample_size])
    prepared, skipped = prepare_vault_sparklines(prices_df, selected_ids)
    preparation_seconds = time.perf_counter() - started

    render_started = time.perf_counter()
    rendered = render_sparklines(prepared, max_workers=int(os.environ.get("SPARKLINE_MAX_WORKERS", "8")))
    render_seconds = time.perf_counter() - render_started
    compression_started = time.perf_counter()
    compressed = [gzip.compress(image["payload"], mtime=0) for image in rendered]
    compression_seconds = time.perf_counter() - compression_started

    rows = [
        ["eligible IDs", len(included_ids)],
        ["sample IDs", len(selected_ids)],
        ["prepared vaults", len(prepared)],
        ["insufficient history", skipped],
        ["rendered images", len(rendered)],
        ["preparation seconds", f"{preparation_seconds:.3f}"],
        ["render seconds", f"{render_seconds:.3f}"],
        ["compression seconds", f"{compression_seconds:.3f}"],
        ["uncompressed bytes", sum(len(image["payload"]) for image in rendered)],
        ["compressed bytes", sum(len(payload) for payload in compressed)],
    ]
    print(tabulate(rows, headers=["Metric", "Value"], tablefmt="github"))


if __name__ == "__main__":
    main()

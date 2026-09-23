#!/usr/bin/env python3
"""Compare vault identity cross-check strategies on a production-format snapshot."""

import json
import os
import resource
import time
from pathlib import Path

import pandas as pd

from eth_defi.research.vault_metrics import cross_check_data
from eth_defi.vault.vaultdb import VaultDatabase


def main() -> None:
    """Load the same identity snapshot and time one selected cross-check.

    ``BENCHMARK_VARIANT`` selects the existing or distinct-pair strategy.
    The script writes one JSON result to ``BENCHMARK_RESULT_PATH``.

    :return: ``None`` after saving the measurement.
    """
    variant = os.environ["BENCHMARK_VARIANT"]
    assert variant in {"reference", "distinct_pairs"}
    data_dir = Path(os.environ["PIPELINE_DATA_DIR"])
    vault_db = VaultDatabase.read(data_dir / "vault-metadata-db.pickle")
    prices_df = pd.read_parquet(data_dir / "cleaned-vault-prices-1h.parquet", columns=["chain", "address"])
    errors: list[str] = []

    started_at = time.perf_counter()
    if variant == "reference":
        count = cross_check_data(vault_db, prices_df, printer=errors.append)
    else:
        vault_ids = {key.as_string_id() for key in vault_db.keys()}
        pairs = prices_df[["chain", "address"]].drop_duplicates()
        price_ids = set(pairs["chain"].astype(str) + "-" + pairs["address"].astype(str))
        missing = price_ids - vault_ids
        errors.extend(f"Price data has entry {entry} that is not in vault database" for entry in missing)
        count = len(missing)
    elapsed = time.perf_counter() - started_at

    result = {
        "variant": variant,
        "rows": len(prices_df),
        "errors": count,
        "error_messages": sorted(errors),
        "wall_seconds": round(elapsed, 3),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    Path(os.environ["BENCHMARK_RESULT_PATH"]).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)  # noqa: T201 - observable standalone benchmark


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Profile a bounded sample of the real top-vault metric loop.

Read the production-format local Parquet and metadata without changing either.
Daily preparation runs outside the profiler so the saved statistics describe
only ``calculate_lifetime_metrics`` and its callees.
"""

import cProfile
import io
import json
import os
import pstats
import resource
import time
from pathlib import Path

import pandas as pd

from eth_defi.feed.stablecoin_rate import load_stablecoin_rate_targets
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics
from eth_defi.stablecoin_metadata import STABLECOINS_DATA_DIR
from eth_defi.token import is_stablecoin_like
from eth_defi.vault.curator import load_curator_map
from eth_defi.vault.vaultdb import VaultDatabase


def main() -> None:  # noqa: PLR0914 - bounded benchmark keeps input and profiler state local
    """Profile 250 spaced stablecoin vaults and save the result.

    The sample is spread across sorted vault IDs to avoid depending on one
    chain or protocol. The resulting timings describe the sample only.

    :return: ``None`` after writing profile and input metadata artefacts.
    """
    source_dir = Path(os.environ["PIPELINE_DATA_DIR"]).expanduser()
    result_dir = Path(os.environ["BENCHMARK_RESULT_DIR"])
    cache_phase = os.environ.get("BENCHMARK_CACHE_PHASE", "cold")
    assert cache_phase in {"cold", "warm"}
    result_dir.mkdir(parents=True, exist_ok=True)
    price_path = source_dir / "cleaned-vault-prices-1h.parquet"
    vault_db = VaultDatabase.read(source_dir / "vault-metadata-db.pickle")
    allowed_ids = {f"{vault['_detection_data'].chain}-{vault['_detection_data'].address}" for vault in vault_db.values() if is_stablecoin_like(vault["Denomination"])}
    available_ids = set(pd.read_parquet(price_path, columns=["id"])["id"].dropna().unique())
    candidate_ids = sorted(allowed_ids & available_ids)
    sample_size = min(250, len(candidate_ids))
    selected_ids = [candidate_ids[index * len(candidate_ids) // sample_size] for index in range(sample_size)]
    prices = pd.read_parquet(price_path, filters=[("id", "in", selected_ids)])
    daily = calculate_hourly_returns_for_all_vaults(prices)
    del prices

    if cache_phase == "warm":
        load_stablecoin_rate_targets(STABLECOINS_DATA_DIR)
        load_curator_map()

    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    metrics = calculate_lifetime_metrics(daily, vault_db)
    profiler.disable()
    wall_seconds = time.perf_counter() - started

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).strip_dirs()
    stats.sort_stats("cumulative").print_stats(40)
    stats.sort_stats("tottime").print_stats(40)
    profile_name = "metrics-cprofile" if cache_phase == "cold" else "metrics-cprofile-warm"
    (result_dir / f"{profile_name}.txt").write_text(stream.getvalue(), encoding="utf-8")
    summary = {
        "sample_vaults": sample_size,
        "daily_rows": len(daily),
        "metrics_rows": len(metrics),
        "wall_seconds": round(wall_seconds, 3),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "price_path": str(price_path),
        "price_mtime_ns": price_path.stat().st_mtime_ns,
        "scope": "calculate_lifetime_metrics and callees only; cProfile instrumentation adds overhead",
        "cache_phase": cache_phase,
    }
    (result_dir / f"{profile_name}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True), flush=True)  # noqa: T201 - standalone profile result


if __name__ == "__main__":
    main()

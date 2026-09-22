"""Benchmark the native ETH/BTC crypto metadata fast path locally.

The benchmark compares the previous full-frame route with the projected native
route using exactly the same qualifying vaults and rows. Admission reduction is
reported separately, so it is not misrepresented as algorithmic speed-up. The
script reads local files only and never changes bundle data or uploads to R2.

Environment variables:

- ``PIPELINE_DATA_DIR``: Pipeline data directory.
- ``VAULT_DATABASE``: Vault metadata pickle override.
- ``CRYPTO_BENCHMARK_PRICE_DATABASE``: Cleaned crypto Parquet override.
- ``CRYPTO_BENCHMARK_N``: Number of deterministic native candidates; ``0``
  means all native candidates. Defaults to ``50``.
- ``CRYPTO_BENCHMARK_REPEATS``: Number of timed repetitions. Defaults to ``1``.
"""

import gc
import hashlib
import os
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from tabulate import tabulate

from eth_defi.feed.stablecoin_rate import StablecoinRateFeeder
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics
from eth_defi.utils import setup_console_logging
from eth_defi.vault import crypto_vaults
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir


def _resolve_path(value: str | None, default: Path) -> Path:
    """Resolve one environment path against the benchmark default.

    Environment overrides support retained production-data copies without
    changing the scanner's configured pipeline directory.

    :param value:
        Optional environment value.
    :param default:
        Path used when the environment value is absent.
    :return:
        Expanded override or the supplied default path.
    """
    return Path(value).expanduser() if value else default


def _measure(operation: Callable[[], pd.DataFrame]) -> tuple[float, int]:
    """Measure one metric operation's wall time and output count.

    Garbage collection runs before each route to reduce noise from the prior
    calculation. Memory is deliberately excluded because two routes in one
    interpreter do not produce comparable process high-water marks.

    :param operation:
        Zero-argument metric calculation to time.
    :return:
        Elapsed seconds and number of generated metric records.
    """
    gc.collect()
    started = time.perf_counter()
    metrics = operation()
    return time.perf_counter() - started, len(metrics)


def _deterministic_ids(ids: frozenset[str], limit: int) -> list[str]:
    """Choose stable vault IDs by SHA-256 rather than source row order.

    Digest ordering makes repeated runs select the same candidate set even
    when a Parquet rewrite changes physical row ordering.

    :param ids:
        Available native vault IDs.
    :param limit:
        Maximum number to select, or a non-positive value for all IDs.
    :return:
        Deterministically ordered vault IDs.
    """
    ordered = sorted(ids, key=lambda vault_id: hashlib.sha256(vault_id.encode("utf-8")).hexdigest())
    return ordered if limit <= 0 else ordered[:limit]


def main() -> None:  # noqa: PLR0914 - benchmark setup keeps all compared inputs visible
    """Run and print a same-input native metadata benchmark.

    Candidate admission is calculated once, after which both old and new
    routes receive only the same qualifying rows. The output therefore keeps
    threshold savings separate from route speed-up.

    :return:
        ``None`` after printing timing and admission tables.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    data_dir = get_pipeline_data_dir()
    vault_db_path = _resolve_path(os.environ.get("VAULT_DATABASE"), data_dir / "vault-metadata-db.pickle")
    default_price_path = crypto_vaults.resolve_crypto_vault_paths(data_dir).cleaned_price_path
    price_path = _resolve_path(os.environ.get("CRYPTO_BENCHMARK_PRICE_DATABASE"), default_price_path)
    limit = int(os.environ.get("CRYPTO_BENCHMARK_N", "50"))
    repeats = max(1, int(os.environ.get("CRYPTO_BENCHMARK_REPEATS", "1")))

    vault_db = VaultDatabase.read(vault_db_path)
    prices_df = pd.read_parquet(price_path)
    if not isinstance(prices_df.index, pd.DatetimeIndex):
        prices_df["timestamp"] = pd.to_datetime(prices_df["timestamp"])
        prices_df.set_index("timestamp", inplace=True)
    prices_df["id"] = prices_df["id"].astype(str)
    admission = crypto_vaults.calculate_crypto_native_admission(vault_db, prices_df)
    selected_ids = _deterministic_ids(admission.native_ids, limit)
    qualifying_ids = set(selected_ids).intersection(admission.qualifying_ids)
    benchmark_prices = prices_df.loc[prices_df["id"].isin(qualifying_ids)]
    benchmark_rows = {spec: row for spec, row in vault_db.rows.items() if spec.as_string_id() in qualifying_ids}
    benchmark_db = VaultDatabase(rows=benchmark_rows)
    stablecoin_rate_feeder = StablecoinRateFeeder()

    def run_old() -> pd.DataFrame:
        """Run the pre-optimisation route on qualifying rows.

        :return:
            One metric record per successfully processed vault.
        """
        daily_prices = calculate_hourly_returns_for_all_vaults(benchmark_prices)
        return calculate_lifetime_metrics(daily_prices, benchmark_rows, stablecoin_rate_feeder=stablecoin_rate_feeder)

    def run_new() -> pd.DataFrame:
        """Run the projected native route on the same qualifying rows.

        :return:
            One metric record per successfully processed vault.
        """
        return crypto_vaults._build_native_crypto_metrics(benchmark_prices, benchmark_db, stablecoin_rate_feeder, None)

    results: list[dict[str, Any]] = []
    for repeat in range(1, repeats + 1):
        old_seconds, old_vaults = _measure(run_old)
        new_seconds, new_vaults = _measure(run_new)
        results.extend(
            [
                {
                    "repeat": repeat,
                    "route": "old-full-frame",
                    "seconds": old_seconds,
                    "input rows": len(benchmark_prices),
                    "metric vaults": old_vaults,
                    "speed-up": 1.0,
                },
                {
                    "repeat": repeat,
                    "route": "new-native-projected",
                    "seconds": new_seconds,
                    "input rows": len(benchmark_prices),
                    "metric vaults": new_vaults,
                    "speed-up": old_seconds / new_seconds,
                },
            ]
        )

    print(tabulate(results, headers="keys", tablefmt="rounded_outline", floatfmt=".3f"))
    print(
        tabulate(
            [
                {
                    "all native vaults": len(admission.native_ids),
                    "all qualifying vaults": len(admission.qualifying_ids),
                    "selected candidates": len(selected_ids),
                    "selected qualifying": len(qualifying_ids),
                    "selected rejected": len(selected_ids) - len(qualifying_ids),
                    "price file": str(price_path),
                }
            ],
            headers="keys",
            tablefmt="rounded_outline",
        )
    )


if __name__ == "__main__":
    main()

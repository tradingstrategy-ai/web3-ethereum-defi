"""Populate stablecoin YAML rate metadata from CoinGecko.

This helper is intended for initial one-off population and manual re-runs. The
post scanner later calls the same library refresh path through its own durable
24-hour gate.

Environment variables:

- ``STABLECOIN_DATA_DIR``: Optional. Stablecoin YAML directory.
- ``FORCE``: Optional. Set to ``true`` to bypass per-entry daily gates.
- ``STABLECOIN_RATE_TIMEOUT``: Optional. CoinGecko timeout in seconds. Default: 20.
- ``COINGECKO_ID_MAPPING_FILE``: Optional. JSON file with explicit id mappings.
- ``COINGECKO_DEMO_API_KEY``: Optional. CoinGecko demo API key read by the rate module.
- ``PROGRESS``: Optional. Set to ``false`` to hide tqdm progress bars. Default: true.
- ``LOG_LEVEL``: Optional. Default: info.
"""

import logging
import os
from collections import Counter
from pathlib import Path

from tabulate import tabulate

from eth_defi.feed.stablecoin_rate import apply_coingecko_mapping_file, iter_stablecoin_rate_targets, refresh_stablecoin_rates
from eth_defi.stablecoin_metadata import STABLECOINS_DATA_DIR
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y"}


def main() -> int:
    """Run the stablecoin rate population helper."""
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/populate-stablecoin-rates.log"),
    )

    data_dir_raw = os.environ.get("STABLECOIN_DATA_DIR")
    data_dir = Path(data_dir_raw).expanduser() if data_dir_raw else STABLECOINS_DATA_DIR
    force = _env_bool("FORCE")
    progress_bar = _env_bool("PROGRESS", default=True)
    timeout = float(os.environ.get("STABLECOIN_RATE_TIMEOUT", "20"))

    logger.info("Starting stablecoin rate population")
    logger.info("Stablecoin data directory: %s", data_dir)
    logger.info("Force refresh: %s", force)
    logger.info("CoinGecko timeout: %.1f seconds", timeout)
    logger.info("Progress bars: %s", progress_bar)

    mapping_path_raw = os.environ.get("COINGECKO_ID_MAPPING_FILE")
    mappings_applied = 0
    if mapping_path_raw:
        mapping_path = Path(mapping_path_raw).expanduser()
        logger.info("Applying CoinGecko id mapping file: %s", mapping_path)
        mappings_applied = apply_coingecko_mapping_file(data_dir, mapping_path, progress_bar=progress_bar)
        logger.info("Applied %d CoinGecko id mapping entries", mappings_applied)

    logger.info("Refreshing stablecoin rates from CoinGecko")
    summary = refresh_stablecoin_rates(
        data_dir=data_dir,
        force=force,
        timeout=timeout,
        progress_bar=progress_bar,
    )
    logger.info(
        "Stablecoin rate population finished: files_scanned=%d entries_seen=%d rates_fetched=%d files_updated=%d failures=%d depegged=%d",
        summary.files_scanned,
        summary.entries_seen,
        summary.rates_fetched,
        summary.files_updated,
        summary.failed_count,
        summary.depegged_count,
    )
    _log_population_details(data_dir)

    rows = [
        ["Mappings applied", mappings_applied],
        ["Files scanned", summary.files_scanned],
        ["Entries seen", summary.entries_seen],
        ["Rates fetched", summary.rates_fetched],
        ["Files updated", summary.files_updated],
        ["CoinGecko ids checked", summary.coingecko_ids_checked],
        ["CoinGecko ids valid", summary.coingecko_ids_valid],
        ["CoinGecko id validation failures", summary.coingecko_id_validation_failed_count],
        ["Depegged", summary.depegged_count],
        ["Unactionable depegged", summary.unactionable_depegged_count],
        ["Missing CoinGecko ids", summary.skipped_missing_coingecko],
        ["Unknown pegs", summary.skipped_unknown_peg],
        ["Failures", summary.failed_count],
    ]
    print(tabulate(rows, headers=["Metric", "Value"], tablefmt="fancy_grid"))
    return 0


def _log_population_details(data_dir: Path) -> None:
    """Log depeg and failure details after a population run."""
    targets = list(iter_stablecoin_rate_targets(data_dir))
    failure_reasons = Counter(target.rate_fetch_failed_reason for target in targets if target.rate_fetch_failed_reason)
    if failure_reasons:
        logger.warning("Stablecoin rate failure reasons: %s", dict(failure_reasons))

    for target in targets:
        if target.depegged_at:
            logger.warning(
                "Depegged stablecoin: slug=%s symbol=%s name=%s usd_rate=%s peg_rate=%s peg_currency=%s",
                target.slug,
                target.symbol,
                target.name,
                target.usd_rate,
                target.peg_rate,
                target.peg_rate_currency,
            )


if __name__ == "__main__":
    raise SystemExit(main())

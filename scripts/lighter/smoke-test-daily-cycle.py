"""Run an isolated live Lighter database population smoke test.

Scans a small number of pools from every configured Lighter deployment into a
new temporary DuckDB, closes and reopens the database, and validates metadata,
daily prices, snapshots, ownership, source accounting fields, and source JSON.

The script never opens the production Lighter database. By default it creates
and retains a unique directory under the system temporary directory.

Usage:

.. code-block:: shell

    LOG_LEVEL=info poetry run python scripts/lighter/smoke-test-daily-cycle.py

    LIGHTER_TEST_OUTPUT_DIR=/tmp/lighter-cycle-check \
      MAX_POOLS=5 \
      MAX_WORKERS=2 \
      poetry run python scripts/lighter/smoke-test-daily-cycle.py

Environment variables:

- ``LIGHTER_TEST_OUTPUT_DIR``: New output directory. Defaults to a unique
  temporary directory. The script refuses a non-empty directory.
- ``MIN_TVL``: Minimum pool TVL. Defaults to ``0``.
- ``MAX_POOLS``: Maximum pools scanned per deployment. Defaults to ``3``.
- ``MAX_WORKERS``: Thread workers per deployment. Defaults to ``2``.
- ``HTTP_TIMEOUT``: Lighter API request timeout in seconds. Defaults to ``30``.
- ``LOG_LEVEL``: Logging level. Defaults to ``info``.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from eth_defi.lighter.constants import LIGHTER_DEPLOYMENTS, LighterAPIConfig
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase, run_daily_scan
from eth_defi.lighter.session import create_lighter_session
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

SOURCE_ACCOUNTING_COLUMNS: tuple[str, ...] = (
    "cumulative_pool_inflow",
    "cumulative_pool_outflow",
    "cumulative_account_inflow",
    "cumulative_account_outflow",
    "cumulative_spot_inflow",
    "cumulative_spot_outflow",
    "cumulative_staking_inflow",
    "cumulative_staking_outflow",
    "trade_pnl",
    "trade_spot_pnl",
    "pool_pnl",
    "staking_pnl",
    "volume",
)


def _resolve_output_directory() -> Path:
    """Create the isolated directory used by this smoke test.

    A caller-provided directory is accepted only when it is empty. This
    prevents a manual test from mutating production or a previous test run
    accidentally.

    :return:
        Existing empty or newly created output directory.
    """
    configured_path = os.environ.get("LIGHTER_TEST_OUTPUT_DIR")
    if configured_path:
        output_dir = Path(configured_path).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        if any(output_dir.iterdir()):
            raise FileExistsError(f"Smoke-test output directory must be empty: {output_dir}")
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="lighter-cycle-smoke-test-"))

    return output_dir


def _scan_deployment(
    deployment: LighterAPIConfig,
    *,
    database_path: Path,
    output_dir: Path,
    min_tvl: float,
    max_pools: int,
    max_workers: int,
    timeout: float,
) -> None:
    """Scan one Lighter deployment into the shared smoke-test database.

    Each deployment receives an isolated rate-limit database while Ethereum
    and Robinhood intentionally share the metrics DuckDB being validated.

    :param deployment:
        Lighter deployment configuration.
    :param database_path:
        Isolated metrics DuckDB path.
    :param output_dir:
        Directory for deployment-specific rate-limit state.
    :param min_tvl:
        Minimum pool TVL accepted by the scanner.
    :param max_pools:
        Maximum pools scanned for this deployment.
    :param max_workers:
        Thread worker count.
    :param timeout:
        Per-request HTTP timeout in seconds.
    :return:
        None.
    """
    logger.info("Scanning %s into %s", deployment.name, database_path)
    rate_limit_path = output_dir / f"{deployment.slug}-rate-limit.sqlite"
    with create_lighter_session(
        deployment=deployment,
        rate_limit_db_path=rate_limit_path,
        pool_maxsize=max_workers,
    ) as session:
        database = run_daily_scan(
            session=session,
            db_path=database_path,
            min_tvl=min_tvl,
            max_pools=max_pools,
            max_workers=max_workers,
            timeout=timeout,
        )
        database.close()


def _count_valid_source_json(snapshots: pd.DataFrame) -> int:
    """Count snapshots containing valid source-account JSON objects.

    :param snapshots:
        Latest point-in-time snapshot rows.
    :return:
        Number of rows containing a JSON object.
    """

    def _is_json_object(value: object) -> bool:
        """Validate one serialised source account.

        :param value:
            DuckDB source JSON cell.
        :return:
            ``True`` when the cell contains a JSON object.
        """
        if not isinstance(value, str):
            return False
        try:
            return isinstance(json.loads(value), dict)
        except json.JSONDecodeError:
            return False

    return int(snapshots["source_account_json"].apply(_is_json_object).sum())


def _summarise_deployment(
    deployment: LighterAPIConfig,
    *,
    metadata: pd.DataFrame,
    prices: pd.DataFrame,
    snapshots: pd.DataFrame,
) -> tuple[dict[str, object], list[str]]:
    """Validate and summarise rows for one Lighter deployment.

    :param deployment:
        Lighter deployment being validated.
    :param metadata:
        All pool metadata rows in the smoke-test database.
    :param prices:
        All daily price rows in the smoke-test database.
    :param snapshots:
        Latest snapshot rows in the smoke-test database.
    :return:
        Terminal summary row and validation failures.
    """
    deployment_metadata = metadata[metadata["deployment"] == deployment.slug]
    deployment_prices = prices[prices["deployment"] == deployment.slug]
    deployment_snapshots = snapshots[snapshots["deployment"] == deployment.slug]

    price_pool_count = int(deployment_prices["account_index"].nunique())
    snapshot_pool_count = int(deployment_snapshots["account_index"].nunique())
    ownership_rows = int(deployment_metadata[["total_shares", "operator_shares"]].notna().all(axis=1).sum())
    accounting_rows = int(deployment_prices[list(SOURCE_ACCOUNTING_COLUMNS)].notna().any(axis=1).sum())
    valid_json_rows = _count_valid_source_json(deployment_snapshots) if not deployment_snapshots.empty else 0

    summary = {
        "deployment": deployment.slug,
        "metadata pools": len(deployment_metadata),
        "price pools": price_pool_count,
        "daily rows": len(deployment_prices),
        "snapshots": snapshot_pool_count,
        "source JSON rows": valid_json_rows,
        "ownership rows": ownership_rows,
        "accounting rows": accounting_rows,
        "first price": deployment_prices["date"].min() if not deployment_prices.empty else None,
        "last price": deployment_prices["date"].max() if not deployment_prices.empty else None,
    }
    required_counts = {
        "metadata pools": len(deployment_metadata),
        "price pools": price_pool_count,
        "daily rows": len(deployment_prices),
        "snapshots": snapshot_pool_count,
        "ownership rows": ownership_rows,
        "accounting rows": accounting_rows,
    }
    failures = [f"{deployment.slug}: no {label}" for label, count in required_counts.items() if count == 0]
    if valid_json_rows != len(deployment_snapshots):
        failures.append(f"{deployment.slug}: {len(deployment_snapshots) - valid_json_rows} snapshots have invalid source_account_json")
    return summary, failures


def _summarise_observations(
    deployment: LighterAPIConfig,
    *,
    accounts: pd.DataFrame,
    positions: pd.DataFrame,
    payloads: pd.DataFrame,
) -> tuple[dict[str, int], list[str]]:
    """Validate and summarise common perp observations for one deployment.

    A successful source read may contain no open positions, so position rows
    are reported but not required. Every account observation must, however,
    have an available position set and a persisted source payload.

    :param deployment:
        Lighter deployment being validated.
    :param accounts:
        Common perp account observations.
    :param positions:
        Common perp position observations.
    :param payloads:
        Persisted raw source payloads.
    :return:
        Terminal summary fields and validation failures.
    """
    deployment_accounts = accounts[accounts["deployment_slug"] == deployment.slug]
    available_count = int((deployment_accounts["position_data_status"] == "available").sum())
    deployment_positions = positions[positions["snapshot_id"].isin(deployment_accounts["snapshot_id"])]
    deployment_payloads = payloads[payloads["deployment_slug"] == deployment.slug]
    summary = {
        "account observations": len(deployment_accounts),
        "available observations": available_count,
        "position rows": len(deployment_positions),
        "source payloads": len(deployment_payloads),
    }
    failures: list[str] = []
    if deployment_accounts.empty:
        failures.append(f"{deployment.slug}: no account observations")
    elif available_count != len(deployment_accounts):
        failures.append(f"{deployment.slug}: {len(deployment_accounts) - available_count} account observations are unavailable")
    if deployment_payloads.empty:
        failures.append(f"{deployment.slug}: no source payloads")
    return summary, failures


def _validate_database(database_path: Path) -> list[dict[str, object]]:
    """Reopen and validate the populated Lighter DuckDB.

    The smoke test requires each deployment to contain metadata, daily prices,
    append-only snapshots, valid source JSON, ownership observations, and at
    least one daily row with source accounting data.

    :param database_path:
        Closed smoke-test DuckDB produced by the live scans.
    :return:
        Per-deployment summary rows for terminal display.
    :raises RuntimeError:
        If any required population check fails.
    """
    database = LighterDailyMetricsDatabase(database_path)
    failures: list[str] = []
    summaries: list[dict[str, object]] = []
    try:
        metadata = database.get_all_pool_metadata()
        prices = database.get_all_daily_prices()
        snapshots = database.get_latest_pool_snapshots()
        accounts = database.con.execute("SELECT * FROM perp_vault_account_observations").fetchdf()
        positions = database.con.execute("SELECT * FROM perp_vault_position_observations").fetchdf()
        payloads = database.con.execute("SELECT * FROM perp_vault_source_payloads").fetchdf()

        expected_price_columns = {"deployment", "account_index", "date", "share_price", "total_shares", *SOURCE_ACCOUNTING_COLUMNS}
        missing_price_columns = expected_price_columns - set(prices.columns)
        if missing_price_columns:
            failures.append(f"pool_daily_prices is missing columns: {sorted(missing_price_columns)}")

        for deployment in LIGHTER_DEPLOYMENTS:
            summary, deployment_failures = _summarise_deployment(
                deployment,
                metadata=metadata,
                prices=prices,
                snapshots=snapshots,
            )
            observation_summary, observation_failures = _summarise_observations(
                deployment,
                accounts=accounts,
                positions=positions,
                payloads=payloads,
            )
            summary.update(observation_summary)
            summaries.append(summary)
            failures.extend(deployment_failures)
            failures.extend(observation_failures)

        database.save()
    finally:
        database.close()

    if failures:
        raise RuntimeError("Lighter database population validation failed:\n- " + "\n- ".join(failures))
    return summaries


def main() -> None:
    """Run both live Lighter scans and validate their isolated DuckDB.

    Configuration is read only from environment variables. Test artefacts are
    deliberately retained so operators can inspect the exact database after a
    successful or failed run.

    :return:
        None.
    """
    output_dir = _resolve_output_directory()
    database_path = output_dir / "lighter-pools.duckdb"
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=output_dir / "lighter-cycle-smoke-test.log",
    )

    min_tvl = float(os.environ.get("MIN_TVL", "0"))
    max_pools = int(os.environ.get("MAX_POOLS", "3"))
    max_workers = int(os.environ.get("MAX_WORKERS", "2"))
    timeout = float(os.environ.get("HTTP_TIMEOUT", "30"))

    if min_tvl < 0:
        raise ValueError(f"MIN_TVL must not be negative, got {min_tvl}")
    if max_pools <= 0:
        raise ValueError(f"MAX_POOLS must be positive, got {max_pools}")
    if max_workers <= 0:
        raise ValueError(f"MAX_WORKERS must be positive, got {max_workers}")
    if timeout <= 0:
        raise ValueError(f"HTTP_TIMEOUT must be positive, got {timeout}")

    logger.info(
        "Starting isolated Lighter cycle smoke test: output=%s, min_tvl=%f, max_pools=%d, max_workers=%d",
        output_dir,
        min_tvl,
        max_pools,
        max_workers,
    )

    for deployment in LIGHTER_DEPLOYMENTS:
        _scan_deployment(
            deployment=deployment,
            database_path=database_path,
            output_dir=output_dir,
            min_tvl=min_tvl,
            max_pools=max_pools,
            max_workers=max_workers,
            timeout=timeout,
        )

    summaries = _validate_database(database_path)
    print(tabulate(summaries, headers="keys", tablefmt="rounded_outline"))
    logger.info("Lighter cycle smoke test passed. Database retained at %s", database_path)


if __name__ == "__main__":
    main()

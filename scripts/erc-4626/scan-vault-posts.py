"""Collect vault-related posts from RSS feeds, social bridges, and X API.

Thin wrapper around :func:`eth_defi.feed.scanner.run_post_scan_cycle`.
All scan logic lives in library modules so integration tests can call them directly.

Usage:

.. code-block:: shell

    # Single run
    LOOP_INTERVAL_SECONDS=0 poetry run python scripts/erc-4626/scan-vault-posts.py

    # Continuous 8-hour loop (default)
    poetry run python scripts/erc-4626/scan-vault-posts.py

    # Limited test run (10 sources per type)
    LIMIT=10 LOOP_INTERVAL_SECONDS=0 poetry run python scripts/erc-4626/scan-vault-posts.py

Environment variables:

- ``DB_PATH``: Optional. Path to DuckDB database file.
- ``MAPPINGS_DIR``: Optional. Path to feeder YAML files.
- ``LOG_LEVEL``: Optional. Default: warning.
- ``MAX_WORKERS``: Optional. Default: 8.
- ``MAX_POSTS_PER_SOURCE``: Optional. Default: 20.
- ``REQUEST_TIMEOUT``: Optional. Default: 20.
- ``REQUEST_DELAY_SECONDS``: Optional. Default: 1.
- ``TWITTER_RSS_BASE_URLS``: Optional. Comma-separated RSS bridge base URLs.
- ``TWITTER_BEARER_TOKEN``: Optional. X API v2 bearer token for reading tweets.
- ``TWITTER_CONSUMER_KEY``: Optional. OAuth 1.0a consumer key for list writes.
- ``TWITTER_SECRET_KEY``: Optional. OAuth 1.0a consumer secret for list writes.
- ``TWITTER_ACCESS_TOKEN``: Optional. OAuth 1.0a user access token for list writes.
- ``TWITTER_ACCESS_TOKEN_SECRET``: Optional. OAuth 1.0a user access token secret.
- ``X_LIST_ID``: Optional. X list ID override for the default list.
- ``X_LIST_NAME``: Optional. X list name to resolve when X_LIST_ID is unset.
- ``X_LIST_ADD_DELAY_SECONDS``: Optional. Delay between list member writes. Default: 1.
- ``SYNC_X_LIST``: Optional. Set to "true" for production list sync. Default: false.
- ``WEBSHARE_API_KEY``: Optional. Enable Webshare-backed proxy rotation.
- ``WEBSHARE_PROXY_MODE``: Optional. Webshare proxy pool mode.
- ``MAX_PROXY_ROTATIONS``: Optional. Default: 3.
- ``MAX_POST_AGE_DAYS``: Optional. Default: 365.
- ``LOOP_INTERVAL_SECONDS``: Optional. Default: 28800 (8 hours). Set to 0 for single run.
- ``LIMIT``: Optional. Limit sources per type for test runs.
- ``DEATH_DETECTION_PERIOD``: Optional. Default: 180 days.
"""

import os
import time
from pathlib import Path

from tabulate import tabulate

from eth_defi.feed.constants import DEFAULT_X_LIST_NAME
from eth_defi.feed.database import DEFAULT_VAULT_POST_DATABASE
from eth_defi.feed.scanner import PostScanConfig, run_post_scan_cycle
from eth_defi.feed.sources import FEEDS_DATA_DIR
from eth_defi.utils import setup_console_logging


def _parse_csv(raw_value: str | None) -> list[str]:
    """Parse a comma-separated environment variable into a list."""

    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _format_datetime(value) -> str:
    """Format optional datetimes for dashboard output."""

    if value is None:
        return "-"
    return value.isoformat(sep=" ", timespec="seconds")


def _print_dashboard(summary) -> None:
    """Print run summary and per-source dashboard."""

    rows = [
        ["Sources loaded", summary.sources_loaded],
        ["Sources succeeded", summary.sources_succeeded],
        ["Sources failed", summary.sources_failed],
        ["Feeders all-skipped", summary.feeders_skipped],
        ["Posts fetched", summary.posts_fetched],
        ["Posts inserted", summary.posts_inserted],
    ]
    print(tabulate(rows, headers=["Metric", "Value"], tablefmt="fancy_grid"))

    source_results = summary.source_results or []
    if not source_results:
        return

    loaded_rows = [
        [
            result.feeder_id,
            result.role,
            result.source_type,
            result.status,
            result.posts_fetched,
            result.posts_inserted,
            _format_datetime(result.last_post_published_at),
        ]
        for result in source_results
    ]
    print()
    print(
        tabulate(
            loaded_rows,
            headers=["Feeder", "Role", "Source", "Status", "Fetched", "Inserted", "Last post"],
            tablefmt="fancy_grid",
        )
    )

    failed_results = [r for r in source_results if r.status == "failed"]
    if failed_results:
        failed_rows = [[r.feeder_id, r.role, r.source_type, (r.error or "")[:60]] for r in failed_results]
        print()
        print(
            tabulate(
                failed_rows,
                headers=["Failed feeder", "Role", "Source", "Error"],
                tablefmt="fancy_grid",
            )
        )


def _build_config() -> PostScanConfig:
    """Build scan configuration from environment variables."""

    db_path_str = os.environ.get("DB_PATH")
    mappings_dir_str = os.environ.get("MAPPINGS_DIR")
    limit_str = os.environ.get("LIMIT")

    return PostScanConfig(
        db_path=Path(db_path_str).expanduser() if db_path_str else DEFAULT_VAULT_POST_DATABASE,
        mappings_dir=Path(mappings_dir_str).expanduser() if mappings_dir_str else FEEDS_DATA_DIR,
        max_workers=int(os.environ.get("MAX_WORKERS", "8")),
        max_posts_per_source=int(os.environ.get("MAX_POSTS_PER_SOURCE", "20")),
        request_timeout=float(os.environ.get("REQUEST_TIMEOUT", "20")),
        request_delay_seconds=float(os.environ.get("REQUEST_DELAY_SECONDS", "1")),
        max_post_age_days=int(os.environ.get("MAX_POST_AGE_DAYS", "365")),
        max_proxy_rotations=int(os.environ.get("MAX_PROXY_ROTATIONS", "3")),
        twitter_bearer_token=os.environ.get("TWITTER_BEARER_TOKEN"),
        twitter_consumer_key=os.environ.get("TWITTER_CONSUMER_KEY"),
        twitter_consumer_secret=os.environ.get("TWITTER_SECRET_KEY"),
        twitter_access_token=os.environ.get("TWITTER_ACCESS_TOKEN"),
        twitter_access_token_secret=os.environ.get("TWITTER_ACCESS_TOKEN_SECRET"),
        x_list_id=os.environ.get("X_LIST_ID"),
        x_list_name=os.environ.get("X_LIST_NAME") or DEFAULT_X_LIST_NAME,
        sync_x_list=os.environ.get("SYNC_X_LIST", "").lower() == "true",
        x_list_add_delay_seconds=float(os.environ.get("X_LIST_ADD_DELAY_SECONDS", "1")),
        limit=int(limit_str) if limit_str else None,
        death_detection_days=int(os.environ.get("DEATH_DETECTION_PERIOD", "180")),
        twitter_rss_base_urls=_parse_csv(os.environ.get("TWITTER_RSS_BASE_URLS")),
    )


def main() -> None:
    """Run the vault post collection pipeline with optional looping."""

    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/scan-vault-posts.log"),
    )

    loop_interval = int(os.environ.get("LOOP_INTERVAL_SECONDS", "28800"))
    config = _build_config()
    cycle = 0

    while True:
        cycle += 1
        print(f"\n=== Post scan cycle {cycle} ===")

        summary = run_post_scan_cycle(config)
        _print_dashboard(summary)

        if loop_interval <= 0:
            break

        print(f"\nSleeping {loop_interval}s until next cycle...")
        time.sleep(loop_interval)


if __name__ == "__main__":
    main()

"""Collect vault-related posts from RSS feeds and social feed bridges.

Usage:

.. code-block:: shell

    poetry run python scripts/erc-4626/scan-vault-posts.py

Environment variables:

- ``DB_PATH``: Optional. Path to DuckDB database file.
- ``MAPPINGS_DIR``: Optional. Path to feeder YAML files.
- ``LOG_LEVEL``: Optional. Default: warning.
- ``MAX_WORKERS``: Optional. Default: 8.
- ``MAX_POSTS_PER_SOURCE``: Optional. Default: 20.
- ``REQUEST_TIMEOUT``: Optional. Default: 20.
- ``REQUEST_DELAY_SECONDS``: Optional. Default: 1.
- ``TWITTER_RSS_BASE_URLS``: Optional. Comma-separated list of Nitter or xcancel-style RSS bridge base URLs.
- ``WEBSHARE_API_KEY``: Optional. Enable Webshare-backed proxy rotation for feed requests.
- ``WEBSHARE_PROXY_MODE``: Optional. Select the Webshare proxy pool mode.
- ``MAX_PROXY_ROTATIONS``: Optional. Default: 3.
- ``MAX_POST_AGE_DAYS``: Optional. Default: 365.
"""

import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.feed.collector import collect_posts, fetch_feed_proxy_rotator
from eth_defi.feed.database import DEFAULT_VAULT_POST_DATABASE, VaultPostDatabase
from eth_defi.feed.sources import FEEDS_DATA_DIR, auto_disable_failed_linkedin_sources, load_post_sources
from eth_defi.utils import setup_console_logging


def _parse_twitter_rss_base_urls(raw_value: str | None) -> list[str]:
    """Parse bridge base URLs from an environment variable."""

    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _format_datetime(value) -> str:
    """Format optional datetimes for dashboard output."""

    if value is None:
        return "-"

    return value.isoformat(sep=" ", timespec="seconds")


def _print_source_dashboard(summary) -> None:
    """Print loaded and failed source dashboards."""

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

    failed_results = [result for result in source_results if result.status == "failed"]
    if failed_results:
        failed_rows = [
            [
                result.feeder_id,
                result.role,
                result.source_type,
                (result.error or "")[:60],
            ]
            for result in failed_results
        ]
        print()
        print(
            tabulate(
                failed_rows,
                headers=["Failed feeder", "Role", "Source", "Error"],
                tablefmt="fancy_grid",
            )
        )


def main() -> None:
    """Run the standalone vault post collection pipeline."""

    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/scan-vault-posts.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else DEFAULT_VAULT_POST_DATABASE

    mappings_dir_str = os.environ.get("MAPPINGS_DIR")
    mappings_dir = Path(mappings_dir_str).expanduser() if mappings_dir_str else FEEDS_DATA_DIR

    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    max_posts_per_source = int(os.environ.get("MAX_POSTS_PER_SOURCE", "20"))
    request_timeout = float(os.environ.get("REQUEST_TIMEOUT", "20"))
    request_delay_seconds = float(os.environ.get("REQUEST_DELAY_SECONDS", "1"))
    max_post_age_days = int(os.environ.get("MAX_POST_AGE_DAYS", "365"))
    max_proxy_rotations = int(os.environ.get("MAX_PROXY_ROTATIONS", "3"))
    twitter_rss_base_urls = _parse_twitter_rss_base_urls(os.environ.get("TWITTER_RSS_BASE_URLS"))

    sources = load_post_sources(mappings_dir=mappings_dir)
    db = VaultPostDatabase(db_path)
    proxy_rotator = fetch_feed_proxy_rotator()

    try:
        summary = collect_posts(
            db,
            sources,
            max_workers=max_workers,
            max_posts_per_source=max_posts_per_source,
            request_timeout=request_timeout,
            request_delay_seconds=request_delay_seconds,
            twitter_rss_base_urls=twitter_rss_base_urls,
            proxy_rotator=proxy_rotator,
            max_proxy_rotations=max_proxy_rotations,
        )
        pruned_count = db.prune_posts(max_post_age_days=max_post_age_days)
        db.save()
    finally:
        db.close()

    today_str = native_datetime_utc_now().strftime("%Y-%m-%d")
    auto_disabled_count = auto_disable_failed_linkedin_sources(summary, sources, today_str)

    rows = [
        ["Sources loaded", summary.sources_loaded],
        ["Sources succeeded", summary.sources_succeeded],
        ["Sources failed", summary.sources_failed],
        ["Posts fetched", summary.posts_fetched],
        ["Posts inserted", summary.posts_inserted],
        ["Posts pruned", pruned_count],
        ["LinkedIn feeds auto-disabled", auto_disabled_count],
    ]
    print(tabulate(rows, headers=["Metric", "Value"], tablefmt="fancy_grid"))
    _print_source_dashboard(summary)


if __name__ == "__main__":
    main()

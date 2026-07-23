"""Fetch ApeX Omni vault metrics into a standalone DuckDB database.

No authentication is required. Configuration is supplied only through
environment variables documented in ``README-apex-vaults.md``.
"""

import logging
import time
from pathlib import Path

from eth_defi.apex.config import ApexReaderConfig
from eth_defi.apex.metrics import ApexMetricsDatabase, run_scan
from eth_defi.apex.session import ApexTimeoutPolicy, create_apex_session_pool
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Run one scan or the configured sequential scan loop."""
    setup_console_logging(
        default_log_level="info",
        log_file=Path("logs/apex-vault-metrics.log"),
    )
    config = ApexReaderConfig.from_environment()
    timeout_policy = ApexTimeoutPolicy(
        connect_timeout=config.connect_timeout,
        read_timeout=config.read_timeout,
        request_deadline=config.request_deadline,
        max_retry_delay=config.max_retry_delay,
        max_response_bytes=config.max_response_bytes,
    )
    session_pool = create_apex_session_pool(
        requests_per_second=config.requests_per_second,
        pool_maxsize=config.max_workers,
        timeout_policy=timeout_policy,
    )
    database: ApexMetricsDatabase | None = None
    try:
        database = ApexMetricsDatabase(config.db_path)
        while True:
            cycle_started = time.monotonic()
            result = run_scan(
                session_pool,
                database,
                vault_ids=config.vault_ids,
                max_workers=config.max_workers,
                history_mode=config.history_mode,
                history_refresh_interval=config.history_refresh_interval,
                ranking_timeout=config.ranking_deadline,
                history_timeout=config.history_deadline,
            )
            logger.info(
                "ApeX scan complete: discovered=%d selected=%d histories=%d successful=%d failed=%d database=%s",
                result.discovered_vaults,
                result.selected_vaults,
                result.attempted_histories,
                result.successful_histories,
                result.failed_histories,
                config.db_path,
            )
            if not config.loop:
                break
            elapsed = time.monotonic() - cycle_started
            sleep_seconds = max(0.0, config.scan_interval.total_seconds() - elapsed)
            logger.info("Next ApeX ranking observation in %.1f seconds", sleep_seconds)
            time.sleep(sleep_seconds)
    finally:
        if database is not None:
            database.close()
        session_pool.close()


if __name__ == "__main__":
    main()

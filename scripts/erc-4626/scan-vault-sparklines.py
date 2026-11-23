"""Continuously publish staged vault sparkline snapshots."""

import logging
import time
from pathlib import Path
from pickle import UnpicklingError  # noqa: S403 - catch errors reading the trusted scanner catalogue

import pyarrow as pa

from eth_defi.cloudflare_r2 import R2OperationError
from eth_defi.research.sparkline_export import export_queued_sparklines
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def run_worker(data_dir: Path) -> None:
    """Process new snapshots and due retries without holding the scanner lock.

    :param data_dir: Shared scanner state directory.
    """
    last_snapshot = None
    retry_at = None
    while True:
        try:
            exported = export_queued_sparklines(data_dir, last_snapshot=last_snapshot, retry_at=retry_at)
            if exported is not None:
                last_snapshot, result = exported
                retry_at = result.retry_at
        except (EOFError, OSError, UnpicklingError, pa.ArrowException, R2OperationError, RuntimeError, ValueError):
            logger.exception("Sparkline worker failed to process the latest snapshot, retrying in five minutes")
            time.sleep(300)
            continue
        time.sleep(30)


def main() -> None:
    """Run the worker on the scanner's shared data volume."""
    setup_console_logging(
        log_file=Path("logs/export-spark-lines.log"),
        only_log_file=False,
        clear_log_file=False,
    )
    run_worker(get_pipeline_data_dir())


if __name__ == "__main__":
    main()

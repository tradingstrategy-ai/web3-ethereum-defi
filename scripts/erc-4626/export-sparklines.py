"""Standalone wrapper for bounded vault sparkline publication."""

from __future__ import annotations

import logging
from pathlib import Path

from eth_defi.research.sparkline import MIN_SPARKLINE_HISTORY, SparklineData  # noqa: F401
from eth_defi.research.sparkline_export import (  # noqa: F401
    RenderData,
    get_included_vault_ids,
    latest_total_assets_by_id,
    prepare_vault_sparklines,
    render_sparklines,
    render_vault_sparklines,
    run_sparkline_export,
    upload_sparkline,
    upload_sparklines,
)
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the standalone exporter under the shared pipeline writer lock."""
    setup_console_logging(
        log_file=Path("logs/export-spark-lines.log"),
        only_log_file=False,
        clear_log_file=False,
    )
    data_dir = get_pipeline_data_dir()
    lock_path = (data_dir / "scan-pipeline").resolve()
    with wait_other_writers(lock_path, timeout=120):
        result = run_sparkline_export(
            data_dir=data_dir,
            vault_db_path=data_dir / "vault-metadata-db.pickle",
            prices_path=data_dir / "crypto-vaults" / "crypto-cleaned-vault-prices-1d.parquet",
            state_path=data_dir / "sparkline-export-state.json",
        )
    if not result.success:
        raise RuntimeError(f"Sparkline export completed with {result.counters['failed']} failed vaults")


if __name__ == "__main__":
    main()

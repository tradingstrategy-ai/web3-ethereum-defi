"""Standalone wrapper for bounded vault sparkline publication."""

from __future__ import annotations

import os
from pathlib import Path

from eth_defi.research.sparkline import MIN_SPARKLINE_HISTORY, SparklineData  # noqa: F401
from eth_defi.research.sparkline_export import (  # noqa: F401
    RenderData,
    export_queued_sparklines,
    get_included_vault_ids,
    inline_sparkline_writer,
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


def main() -> None:
    """Publish the latest queued snapshot, or use live inputs for a standalone run."""
    setup_console_logging(
        log_file=Path("logs/export-spark-lines.log"),
        only_log_file=False,
        clear_log_file=False,
    )
    data_dir = get_pipeline_data_dir()
    force = os.environ.get("FORCE_SPARKLINE_EXPORT", "false").lower() == "true"
    exported = export_queued_sparklines(data_dir, force=force, timeout=600)
    if exported is None:
        with wait_other_writers((data_dir / "scan-pipeline").resolve()), inline_sparkline_writer(data_dir):
            result = run_sparkline_export(data_dir=data_dir, force=force)
    else:
        _, result = exported
    if not result.success:
        raise RuntimeError(f"Sparkline export completed with {result.counters['failed']} failed vaults")


if __name__ == "__main__":
    main()

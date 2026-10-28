"""Compare explicit complete UTC date windows in two counter snapshots.

Configure RPC_COUNTER_BEFORE/AFTER and RPC_COUNTER_BEFORE/AFTER_START/END.
This script opens only the specified snapshots, read-only, and makes no RPCs.
"""

import datetime
import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.provider.rpc_counter_comparison import compare_rpc_counter_windows, fetch_rpc_counter_window
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Report daily request rates and independently available operation detail.

    Required inputs make incident and healthy baseline windows explicit.
    Missing completion outcomes in old counters are never inferred as success.

    :return: None; displays comparisons and coverage diagnostics.
    """
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    windows = [fetch_rpc_counter_window(Path(os.environ[f"RPC_COUNTER_{period}"]).expanduser(), datetime.date.fromisoformat(os.environ[f"RPC_COUNTER_{period}_START"]), datetime.date.fromisoformat(os.environ[f"RPC_COUNTER_{period}_END"])) for period in ("BEFORE", "AFTER")]
    rows = compare_rpc_counter_windows(*windows)
    logger.info("Physical requests per day:\n%s", tabulate([row.values() for row in rows], headers=list(rows[0]) if rows else [], floatfmt=".2f"))
    for name, window in zip(("Before", "After"), windows, strict=True):
        logger.info("%s window %s–%s (exclusive): %d requests, %.2f/day, %d errors", name, window["start"], window["end_exclusive"], window["calls"], window["calls_per_day"], window["errors"])
        logger.info("%s deduplicated phase items and cycles:\n%s", name, tabulate(window["phase_items_and_cycles"], headers=["Chain", "Phase", "Items", "Recorded phase cycles"]))
        logger.info("%s recorded outcomes (empty means unavailable):\n%s", name, tabulate(window["outcomes"], headers=["Chain", "Phase", "Outcome", "Cycles with outcome"]))
        logger.info("%s operation requests (already included in physical totals):\n%s", name, tabulate(window["operations"], headers=["Chain", "Phase", "Operation", "Method", "Provider", "Calls"]))


if __name__ == "__main__":
    main()

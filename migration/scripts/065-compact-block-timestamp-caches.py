"""Compact existing per-chain timestamp databases without losing history.

Purpose: Reclaim internal free blocks and improve ordered integer compression.
Scope: All existing canonical per-chain files in TIMESTAMP_CACHE.
Branch: parquet-md-block-timestamp-duckdb-optimise
PR title: fix: compact block timestamp caches and preserve ordered writes
PR: Not opened.
Preserves: All block/timestamp pairs, gaps, metadata, prices and reader state.
Expected: Exact row comparisons; smaller files replaced with independent backups.

LLM-generated operational migration; changes require human review.

For local per-chain testing, execute this file with TIMESTAMP_CHAIN_IDS set to
comma-separated numeric IDs. DRY_RUN defaults to true. Manual journals live
under the cache root's .timestamp-maintenance/<sorted-ids> directory, separate
from the normal all-chain migration journal. Successful manual runs are skipped.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.vault_protocols.migration import MigrationContext, run_pending_migrations
from eth_defi.vault_protocols.repairs.timestamp_storage import compact_timestamp_caches


def migrate(context: MigrationContext) -> None:
    """Compact timestamp files using the guarded runner and its journal.

    Preview validates scratch candidates only; apply retains per-chain backups.

    :param context: Runner-owned maintenance context.
    :return: ``None`` after successful preview or publication.
    """
    selection = os.environ.get("TIMESTAMP_CHAIN_IDS")
    chain_ids = tuple(sorted(int(value.strip()) for value in selection.split(","))) if selection else None
    if chain_ids is not None:
        root = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser().resolve()
        manual_state = root / ".timestamp-maintenance" / "-".join(map(str, chain_ids))
        if context.data_dir.resolve() != manual_state:
            raise ValueError("TIMESTAMP_CHAIN_IDS is supported only by this script's manual entry point")
    if chain_ids is None:
        compact_timestamp_caches(context)
    else:
        compact_timestamp_caches(context, chain_ids=chain_ids)


def main() -> None:
    """Run only this numbered migration with an isolated per-selection journal.

    Reuse the normal runner's guards, durable journal and retry logic without
    marking the all-chain production migration complete after a partial test.

    :return: ``None`` after preview, apply or a previously successful skip.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    selection = os.environ.get("TIMESTAMP_CHAIN_IDS", "")
    chain_ids = tuple(sorted(int(value.strip()) for value in selection.split(",") if value.strip()))
    if not chain_ids or any(chain <= 0 for chain in chain_ids) or len(set(chain_ids)) != len(chain_ids):
        raise ValueError("Set TIMESTAMP_CHAIN_IDS to distinct positive numeric chain IDs")
    value = os.environ.get("DRY_RUN", "true").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("DRY_RUN must be true or false")
    root = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Timestamp cache directory does not exist: {root}")
    os.environ["TIMESTAMP_CACHE"] = str(root)
    os.environ["TIMESTAMP_CHAIN_IDS"] = ",".join(map(str, chain_ids))
    state = root / ".timestamp-maintenance" / "-".join(map(str, chain_ids))
    if not state.resolve().is_relative_to(root):
        raise ValueError("Manual timestamp journal must remain inside the cache directory")
    state.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="timestamp-manual-scripts-") as temporary:
        scripts = Path(temporary)
        shutil.copy2(__file__, scripts / Path(__file__).name)
        run_pending_migrations(dry_run=value == "true", data_dir=state, scripts_dir=scripts)


if __name__ == "__main__":
    main()

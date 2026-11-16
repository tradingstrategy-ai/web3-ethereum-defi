# ruff: noqa: N999 -- numbered migration filename.
"""Restore dense timestamp coverage inside every existing HyperSync cache.

Purpose: Undo sparse timestamp tails introduced by PR #1321.
Scope: All existing canonical caches supported by HyperSync, from their own
first to last cached block. Unsupported RPC-only chains are reported unchanged.
Branch: fix/dense-block-timestamp-caches
PR title: fix: keep HyperSync timestamp databases dense and repair old gaps
PR: Not opened.
Preserves: Every original timestamp row, metadata, prices, leads and reader state.
Expected: Every supported non-empty cache has last-first+1 rows and no gaps.

LLM-generated operational migration; changes require human review.

Local per-chain runs use TIMESTAMP_CHAIN_IDS with this numbered file directly,
a separate dense-<ids> journal, and DRY_RUN=true by default. Close local cache
users first. Production uses the normal pending batch without a selection.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.vault_protocols.migration import MigrationContext, run_pending_migrations
from eth_defi.vault_protocols.repairs.dense_timestamps import repair_dense_timestamp_caches


def migrate(context: MigrationContext) -> None:
    """Fill historical timestamp gaps using the guarded runner and journal.

    Preview reports missing rows; apply validates resumable, dense candidates.

    :param context: Runner-owned maintenance context.
    :return: ``None`` after successful preview or publication.
    """
    selection = os.environ.get("TIMESTAMP_CHAIN_IDS")
    chain_ids = tuple(sorted(int(value.strip()) for value in selection.split(","))) if selection else None
    if chain_ids is not None:
        root = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser().resolve()
        manual_state = root / ".timestamp-maintenance" / ("dense-" + "-".join(map(str, chain_ids)))
        if context.data_dir.resolve() != manual_state:
            msg = "TIMESTAMP_CHAIN_IDS is supported only by this script's manual entry point"
            raise ValueError(msg)
    if chain_ids is None:
        repair_dense_timestamp_caches(context)
    else:
        repair_dense_timestamp_caches(context, chain_ids=chain_ids)


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
        msg = "Set TIMESTAMP_CHAIN_IDS to distinct positive numeric chain IDs"
        raise ValueError(msg)
    value = os.environ.get("DRY_RUN", "true").strip().lower()
    if value not in {"true", "false"}:
        msg = "DRY_RUN must be true or false"
        raise ValueError(msg)
    root = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Timestamp cache directory does not exist: {root}")
    os.environ["TIMESTAMP_CACHE"] = str(root)
    os.environ["TIMESTAMP_CHAIN_IDS"] = ",".join(map(str, chain_ids))
    state = root / ".timestamp-maintenance" / ("dense-" + "-".join(map(str, chain_ids)))
    if not state.resolve().is_relative_to(root):
        msg = "Manual timestamp journal must remain inside the cache directory"
        raise ValueError(msg)
    state.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="timestamp-manual-scripts-") as temporary:
        scripts = Path(temporary)
        shutil.copy2(__file__, scripts / Path(__file__).name)
        run_pending_migrations(dry_run=value == "true", data_dir=state, scripts_dir=scripts)


if __name__ == "__main__":
    main()

"""Best-effort cleanup hooks for vault metadata exports.

The scanner stores raw discovery metadata for protocol-specific readers. The
public JSON export may need a small amount of derived cleanup before metrics
are calculated. Hooks in this module receive only the in-memory
:class:`~eth_defi.vault.vaultdb.VaultDatabase` used by that export.

Every hook is isolated. A broken offchain service or malformed optional record
is logged and the export continues with the data it already has. This is
deliberate: post-processing must not take down the long-running scanner.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from eth_defi.erc_4626.vault_protocol.yearn.export_cleanup import clean_yearn_vault_metadata
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)

VaultExportPostProcessor = Callable[[VaultDatabase], Iterable[str]]

# Keep registration explicit and ordered. Adding a hook should be a one-line
# change and must not require plugin discovery or configuration machinery.
DEFAULT_VAULT_EXPORT_POST_PROCESSORS: tuple[VaultExportPostProcessor, ...] = (clean_yearn_vault_metadata,)


def run_vault_export_post_processors(
    vault_db: VaultDatabase,
    processors: tuple[VaultExportPostProcessor, ...] = DEFAULT_VAULT_EXPORT_POST_PROCESSORS,
) -> set[str]:
    """Run export cleanup hooks without allowing one to stop the pipeline.

    Hooks mutate only ``vault_db`` in memory. Each hook is timed and isolated;
    an exception is logged with a traceback and the next hook still runs. The
    returned IDs are useful to refresh stale sticky-export rows, but a failed
    hook simply contributes no IDs and leaves the original metadata intact.

    :param vault_db:
        In-memory database loaded for the current JSON export.
    :param processors:
        Ordered cleanup hooks. Tests may provide a small explicit tuple.
    :return:
        Union of vault IDs changed by successfully completed hooks.
    """

    changed_vault_ids: set[str] = set()
    for processor in processors:
        name = getattr(processor, "__name__", processor.__class__.__name__)
        started_at = time.perf_counter()
        try:
            changed = set(processor(vault_db) or ())
        except Exception:
            # This is the deliberate last-resort isolation boundary. Hooks are
            # optional cleanup and must never stop the production scanner.
            logger.exception("Vault export post-processor %s failed; continuing without its cleanup", name)
            continue

        changed_vault_ids.update(changed)
        logger.info(
            "Vault export post-processor %s completed: changed=%d elapsed=%.3fs",
            name,
            len(changed),
            time.perf_counter() - started_at,
        )

    return changed_vault_ids

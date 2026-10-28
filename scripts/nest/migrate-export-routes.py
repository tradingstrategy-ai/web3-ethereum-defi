"""Select one Nest chain and deposit route in an existing public JSON export.

Use the recurring exporter's chain preference: Plume, Ethereum, then other
chains alphabetically. Within the chosen chain prefer USDC, USDT, then pUSD.
Each share token appears once. Recalculate category and risk-coverage aggregates
using the selected records' own TVL and returns; balances on omitted chains
are not included. Scanner metadata, price histories, reader state and sticky
qualification records retain all legitimate chain and deposit routes.

Preview, then apply:

.. code-block:: shell

    poetry run python scripts/nest/migrate-export-routes.py
    DRY_RUN=false poetry run python scripts/nest/migrate-export-routes.py

Environment variables:

- ``PIPELINE_DATA_DIR``: scanner data directory, default
  ``~/.tradingstrategy/vaults``. Also determines the shared writer lock.
- ``VAULT_EXPORT_PATH``: existing JSON file, default
  ``<PIPELINE_DATA_DIR>/top_vaults_by_chain.json``.
- ``DRY_RUN``: ``true`` (default) to preview; ``false`` to apply.
- ``LOG_LEVEL``: console logging level, default ``info``.

An applied change creates a unique sibling backup before atomically replacing
the JSON. Unchanged exports are not rewritten. Price observation timestamps
and build provenance are preserved; this is an offline repair, not a rescan.
Publish the repaired file through the normal export/upload pipeline afterwards.
For Arc rows saved as unknown ERC-7540 vaults, use ``migrate-vaults.py`` with
``NETWORKS=arc NEST_SCAN_PRICES=false`` instead: it verifies the contracts,
repairs metadata and rebuilds listings from collected history.
"""

import json
import logging
import os
import tempfile
from contextlib import nullcontext
from pathlib import Path

from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.top_vaults_json import build_strategy_categories_for_export, select_preferred_nest_routes, validate_strict_json_serialisable, write_strict_json
from eth_defi.vault.vaultdb import get_pipeline_data_dir
from eth_defi.xerberus.vault_export import compute_xerberus_export_stats

logger = logging.getLogger(__name__)


def migrate_nest_export(data_dir: Path, *, dry_run: bool, export_path: Path | None = None) -> int:
    """Remove old duplicate Nest listings without changing scanner state.

    Apply the same pool selection as recurring exports. Persistent mode holds
    the scanner writer lock through reading, validation, backup and atomic
    replacement. Dry runs and unchanged files create no backup. Malformed
    JSON or aggregate calculation errors abort before replacing the export.

    :param data_dir: Scanner pipeline directory determining the writer lock.
    :param dry_run: Report changes without writing any files.
    :param export_path: Optional public export override, otherwise the pipeline
        directory's ``top_vaults_by_chain.json``.
    :return: Number of duplicate Nest records removed or proposed for removal.
    """
    data_dir = data_dir.expanduser().resolve()
    export_path = (export_path or data_dir / "top_vaults_by_chain.json").expanduser().resolve()
    lock = nullcontext() if dry_run else wait_other_writers(data_dir / "scan-pipeline", timeout=60)
    logger.info("%s Nest export routes in %s", "Previewing" if dry_run else "Migrating", export_path)
    with lock:
        source = export_path.read_bytes()
        document = json.loads(source)
        validate_strict_json_serialisable(document)
        vaults = document["vaults"]
        selected = list(select_preferred_nest_routes(vaults))
        removed = len(vaults) - len(selected)
        nest_count = sum(record.get("protocol_slug") == "nest" for record in vaults)
        logger.info("Nest listings: %d -> %d; %s %d alternative chain and deposit routes", nest_count, nest_count - removed, "would remove" if dry_run else "removing", removed)
        logger.info("Chain preference: Plume, Ethereum, then alphabetical; asset preference: USDC, USDT, pUSD")
        before_tvl = sum(record.get("current_nav") or 0 for record in vaults if record.get("protocol_slug") == "nest")
        selected_tvl = sum(record.get("current_nav") or 0 for record in selected if record.get("protocol_slug") == "nest")
        logger.info("Nest selected-chain TVL: $%.2f -> $%.2f; balances on omitted chains are excluded", before_tvl, selected_tvl)
        if not removed:
            return 0

        document["vaults"] = selected
        if "categories" in document:
            document["categories"] = build_strategy_categories_for_export(selected)
        if "xerberus_stats" in document:
            document["xerberus_stats"] = compute_xerberus_export_stats(selected)
        for field, slug_field in (("core3_protocols", "protocol_slug"), ("xerberus_protocols", "protocol_slug"), ("curators", "curator_slug")):
            if field in document:
                slugs = {record.get(slug_field) for record in selected}
                document[field] = {slug: metadata for slug, metadata in document[field].items() if slug in slugs}
        validate_strict_json_serialisable(document)

        if not dry_run:
            with tempfile.NamedTemporaryFile(prefix=f"{export_path.name}.before-nest-route-migration-", dir=export_path.parent, delete=False) as backup:
                backup.write(source)
                backup.flush()
                os.fsync(backup.fileno())
                backup_path = Path(backup.name)
            logger.info("Saved original export to %s", backup_path)
            write_strict_json(export_path, document, validated=True)
            logger.info("Repaired %s; scanner metadata, prices and state preserved", export_path)
        return removed


def main() -> None:
    """Run the offline export repair using environment configuration.

    Only an explicit ``DRY_RUN=false`` enables writes. Invalid configuration
    aborts before opening the public export or acquiring its writer lock.

    :return: ``None`` after reporting the proposed or applied repair.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = os.environ.get("DRY_RUN", "true").strip().lower()
    if dry_run not in {"true", "false"}:
        message = "DRY_RUN must be true or false"
        raise ValueError(message)
    export_path = Path(os.environ["VAULT_EXPORT_PATH"]) if os.environ.get("VAULT_EXPORT_PATH") else None
    migrate_nest_export(get_pipeline_data_dir(), dry_run=dry_run == "true", export_path=export_path)


if __name__ == "__main__":
    main()

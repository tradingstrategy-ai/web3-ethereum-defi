#!/usr/bin/env python3
"""Fill missing or label-only Arcadia vault descriptions.

Numbered migrations call the in-memory transformation helpers in this module.
The old filesystem wrappers remain for regression compatibility; production
uses ``vault-migrate`` through ``scripts/run-vault-migrations.sh`` as described
in ``.claude/docs/migration-scripts.md``. The former standalone entrypoint is
retired. Helpers used by numbered scripts must not acquire locks or write files.
"""

import logging
import os
import shutil
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

from eth_typing import HexAddress
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.erc_4626.vault_protocol.arcadia.description import extract_arcadia_short_description, is_arcadia_narrative, resolve_arcadia_descriptions
from eth_defi.erc_4626.vault_protocol.arcadia.offchain_metadata import ArcadiaVaultMetadata, fetch_arcadia_vaults, normalise_text
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)


def migrate_arcadia_vault_metadata(
    vault_db: VaultDatabase,
    metadata_by_chain: dict[int, dict[HexAddress, ArcadiaVaultMetadata] | None],
    *,
    dry_run: bool,
) -> dict[VaultSpec, tuple[str, ...]]:
    """Fill only missing or label-only descriptions in Arcadia rows.

    Existing narratives in either field are retained independently. Missing
    summaries use the existing long narrative where possible. The caller owns
    backup and atomic persistence; dry runs leave the database unchanged.

    :param vault_db: Existing metadata database.
    :param metadata_by_chain: Optional API indices by chain and lowercase address.
    :param dry_run: Report proposed fields without mutating rows when true.
    :return: Changed field names for each affected vault.
    """
    changes = {}
    rows = [(spec, row) for spec, row in vault_db.rows.items() if row.get("Protocol") == "Arcadia Finance"]
    for spec, row in tqdm(rows, desc="Repairing Arcadia descriptions"):
        index = metadata_by_chain.get(spec.chain_id)
        metadata = index.get(HexAddress(spec.vault_address.lower())) if index is not None else None
        name = normalise_text(row.get("Name"))
        asset = normalise_text(row.get("Denomination"))
        descriptions = resolve_arcadia_descriptions(chain_id=spec.chain_id, vault_name=name, asset_symbol=asset, metadata=metadata)
        existing_long = normalise_text(row.get("_description"))
        existing_short = normalise_text(row.get("_short_description"))
        keep_long = is_arcadia_narrative(existing_long, vault_name=name, asset_symbol=asset, metadata=metadata)
        updates = {}
        if not keep_long:
            updates["_description"] = descriptions.description
        if not is_arcadia_narrative(existing_short, vault_name=name, asset_symbol=asset, metadata=metadata):
            updates["_short_description"] = extract_arcadia_short_description(existing_long) if keep_long else descriptions.short_description
        updates = {field: value for field, value in updates.items() if row.get(field) != value}
        if updates:
            changes[spec] = tuple(updates)
            if not dry_run:
                row.update(updates)
    return changes


def _backup_metadata(path: Path) -> Path:
    """Copy a metadata pickle to an unused backup filename.

    Called under the scanner writer lock, immediately before the atomic write.
    A previous backup is never overwritten.

    :param path: Existing metadata pickle.
    :return: Path of the newly created backup.
    """
    backup = path.with_name(f"{path.name}.bak-arcadia-vault-metadata")
    suffix = 2
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-arcadia-vault-metadata-{suffix}")
        suffix += 1
    with path.open("rb") as source, backup.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    return backup


def main() -> None:
    """Run an observable metadata-only repair configured by environment.

    Dry run is the default. Actual writes take the standard pipeline lock,
    back up the pickle and use the database's atomic writer.

    :return: ``None`` after reporting the proposed or applied changes.
    :raises ValueError: If ``DRY_RUN`` is not a recognised boolean.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    value = os.environ.get("DRY_RUN", "true").lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        message = "DRY_RUN must be true or false"
        raise ValueError(message)
    dry_run = value in {"true", "1", "yes"}
    path = Path(os.environ.get("VAULT_DB_PATH", str(get_pipeline_data_dir() / "vault-metadata-db.pickle"))).expanduser().resolve()
    lock = nullcontext() if dry_run else wait_other_writers(path.parent / "scan-pipeline", timeout=60)
    with lock:
        logger.info("Reading Arcadia metadata from %s (dry run: %s)", path, dry_run)
        vault_db = VaultDatabase.read(path)
        chains = sorted({spec.chain_id for spec, row in vault_db.rows.items() if row.get("Protocol") == "Arcadia Finance"})
        with TemporaryDirectory(prefix="arcadia-metadata-") as directory:
            metadata_by_chain = {chain: fetch_arcadia_vaults(chain, cache_path=Path(directory)) for chain in tqdm(chains, desc="Fetching Arcadia metadata")}
        changes = migrate_arcadia_vault_metadata(vault_db, metadata_by_chain, dry_run=dry_run)
        if changes:
            print(tabulate([(spec.chain_id, spec.vault_address, ", ".join(fields)) for spec, fields in changes.items()], headers=("Chain", "Vault", "Changed fields")))
        if changes and not dry_run:
            backup = _backup_metadata(path)
            logger.info("Created metadata backup at %s", backup)
            vault_db.write(path)
        logger.info("Arcadia description repair: %d affected vaults; dry run: %s", len(changes), dry_run)

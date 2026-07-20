#!/usr/bin/env python3
"""Backfill Accountable vault descriptions in the production metadata pickle.

Accountable's API exposes a full ``vault_strategy`` per vault. This script
updates persisted Accountable metadata after the vault adapter changed its
description mapping, without rescanning historical chain events or touching
price data. Address-scoped handwritten strategy overrides remain authoritative.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/fix-accountable-descriptions.py

Optional environment variables:

- ``VAULT_DB``: metadata pickle path, defaults to
  ``~/.tradingstrategy/vaults/vault-metadata-db.pickle``
- ``DRY_RUN``: set to ``true`` to report changes without writing
- ``LOG_LEVEL``: console log level, defaults to ``info``
"""

import datetime
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from eth_defi.erc_4626.vault_protocol.accountable.offchain_metadata import AccountableVaultMetadata, fetch_accountable_vaults
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

ACCOUNTABLE_PROTOCOL_NAME = "Accountable"


@dataclass(slots=True, frozen=True)
class AccountableDescriptionUpdate:
    """Describe a persisted Accountable vault description update.

    :param spec:
        Vault chain/address key in the metadata database.

    :param old_description:
        Existing full description in the pickle.

    :param new_description:
        Full ``vault_strategy`` from Accountable's API.

    :param old_short_description:
        Existing listing description in the pickle.

    :param new_short_description:
        First sentence of ``vault_strategy`` from Accountable's API.
    """

    spec: VaultSpec
    old_description: str | None
    new_description: str
    old_short_description: str | None
    new_short_description: str

    @property
    def changed(self) -> bool:
        """Check whether either persisted description changes.

        :return:
            ``True`` when the full or short description differs.
        """
        return (self.old_description, self.old_short_description) != (self.new_description, self.new_short_description)


def _normalise_metadata_by_address(metadata_by_address: dict[str, AccountableVaultMetadata]) -> dict[str, AccountableVaultMetadata]:
    """Normalise Accountable metadata keys for case-insensitive vault matching.

    :param metadata_by_address:
        Accountable metadata keyed by ERC-4626 share-token address.

    :return:
        Metadata keyed by lower-case share-token address.
    """
    return {address.lower(): metadata for address, metadata in metadata_by_address.items()}


def refresh_accountable_descriptions(
    vault_db: VaultDatabase,
    metadata_by_address: dict[str, AccountableVaultMetadata],
) -> list[AccountableDescriptionUpdate]:
    """Update all persisted Accountable vault descriptions from fresh API metadata.

    All new values are validated before the database is mutated. This prevents
    a partial metadata update if Accountable no longer returns one of the vaults
    stored in the production database.

    :param vault_db:
        Vault metadata database loaded from pickle.

    :param metadata_by_address:
        Fresh Accountable API metadata keyed by ERC-4626 share-token address.

    :return:
        Applied description updates for all Accountable vaults.
    """
    normalised_metadata = _normalise_metadata_by_address(metadata_by_address)
    pending_updates: list[tuple[VaultRow, AccountableDescriptionUpdate]] = []

    for spec, row in vault_db.rows.items():
        if row.get("Protocol") != ACCOUNTABLE_PROTOCOL_NAME:
            continue

        handwritten_metadata = get_handwritten_vault_metadata(spec.chain_id, spec.vault_address)
        if handwritten_metadata:
            description = handwritten_metadata.description
            short_description = handwritten_metadata.short_description
        else:
            metadata = normalised_metadata.get(spec.vault_address.lower())
            if metadata is None:
                raise ValueError(f"Accountable API metadata is missing vault {spec.as_string_id()}")

            description = metadata.get("description")
            short_description = metadata.get("short_description")
        if not description or not short_description:
            raise ValueError(f"Accountable API metadata has no strategy description for {spec.as_string_id()}")

        pending_updates.append(
            (
                row,
                AccountableDescriptionUpdate(
                    spec=spec,
                    old_description=row.get("_description"),
                    new_description=description,
                    old_short_description=row.get("_short_description"),
                    new_short_description=short_description,
                ),
            )
        )

    if not pending_updates:
        msg = "No Accountable vault rows found in the metadata database"
        raise ValueError(msg)

    for row, update in pending_updates:
        row["_description"] = update.new_description
        row["_short_description"] = update.new_short_description

    return [update for _, update in pending_updates]


def main() -> None:
    """Run the Accountable description backfill from environment variables.

    The Accountable cache is deliberately bypassed so that prior cached manager
    descriptions cannot be copied back into the production metadata pickle.
    The output pickle is written atomically by :class:`VaultDatabase`.

    :return:
        None. The function reports updates and writes unless ``DRY_RUN`` is set.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    vault_db_path = Path(os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE))).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() in {"1", "true", "yes"}

    logger.info("Reading vault metadata from %s", vault_db_path)
    vault_db = VaultDatabase.read(vault_db_path)
    logger.info("Fetching fresh Accountable vault strategy metadata")
    metadata_by_address = fetch_accountable_vaults(max_cache_duration=datetime.timedelta(0))
    updates = refresh_accountable_descriptions(vault_db, metadata_by_address)

    changed = [update for update in updates if update.changed]
    logger.info("Refreshed descriptions for %d Accountable vaults, %d changed", len(updates), len(changed))
    for update in changed:
        logger.info(
            "%s: short description %r -> %r",
            update.spec.as_string_id(),
            update.old_short_description,
            update.new_short_description,
        )

    if dry_run:
        logger.info("DRY_RUN=true, not writing %s", vault_db_path)
        return

    vault_db.write(vault_db_path)
    logger.info("Wrote refreshed Accountable descriptions to %s", vault_db_path)


if __name__ == "__main__":
    main()

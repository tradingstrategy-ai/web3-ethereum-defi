"""Refresh Saturn PT-USDat's beginner-friendly description and summary.

Purpose: Persist the maintained copy and frontend glossary links.
Scope: One Accountable share-token deployment on Monad; two description fields.
Branch: check-saturn-pt-vault-description-source
PR title: fix: explain Saturn PT-USDat strategy and glossary terms
PR: Pending; no pull request opened.
Preserves: All other metadata, prices, reader state and timestamp caches.
Expected: Up to one existing row and two changed fields; missing rows reported.
Run after merge, after publishing the glossary; see the Saturn research note.
No network reads or historical price backfill are needed for this copy-only repair.
"""

from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import SATURN_PT_USDAT_VAULT_METADATA
from eth_defi.vault_protocols.migration import MigrationContext

#: Share token, not the loan address used in Accountable's web URL.
TARGET = VaultSpec(143, "0x1a5b77cef5e4b6f806aeac6f6ea9213a617dc7e3")


def migrate(context: MigrationContext) -> None:
    """Refresh only Saturn's persisted descriptions from the maintained copy.

    Check the existing protocol before staging changes. The runner owns preview,
    backup and persistence, and reports a missing target without creating a row.

    :param context: Guarded migration context with isolated staged metadata.
    :return: ``None`` after reporting and committing or previewing changes.
    :raises ValueError: If the existing target is not an Accountable vault.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return
    if row.get("Protocol") != "Accountable":
        raise ValueError(f"Saturn target {TARGET} is not classified as Accountable")
    metadata = SATURN_PT_USDAT_VAULT_METADATA[TARGET.chain_id, TARGET.vault_address]
    fields = {
        "_description": normalise_vault_description(metadata.description),
        "_short_description": normalise_vault_short_description(metadata.short_description),
    }
    updates = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(updates)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates))
    context.commit_metadata()

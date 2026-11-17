"""Refresh the manual deposit closure for Lighter Robinhood LLP.

Purpose: Honour the maintainer's deposit-closure override despite an active API status.
Scope: Synthetic chain 9998, lighter-pool-robinhood-281474976710654;
_flags, _deposit_closed_reason, _deposit_permission and _whitelist_notes only.
Branch: lighter-vault-manual-overrides-deposits-closed
PR title: feat: honour manual Lighter vault flags and close Robinhood LLP deposits
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1792
Preserves: Descriptions, notes, unrelated metadata, prices, leads and timestamp caches.
Expected: At most one existing row and four changed fields. Missing rows are reported.
No network reads or historical price backfill are needed. Safe to retry.

LLM-generated operational migration; changes require human review.
Run after merge: stop competing writers and vault-scanner-looped before preview
and apply, following .claude/docs/migration-scripts.md. From the production checkout:
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
Then use the same command with DRY_RUN=false. Resume normal exports afterwards.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.lighter.constants import LIGHTER_ROBINHOOD, LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext

TARGET = VaultSpec(LIGHTER_ROBINHOOD.chain_id, LIGHTER_ROBINHOOD.format_pool_address(LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX))
FIELDS = ("_deposit_closed_reason", "_deposit_permission", "_whitelist_notes")


def migrate(context: MigrationContext) -> None:
    """Apply the current manual closure while preserving unrelated saved data.

    Use the ordinary Lighter exporter so the migration and recurring scans
    agree. Merge flags rather than discarding other persisted classifications.
    The runner owns preview staging, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target is attributed to another protocol.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    changed_fields = 0
    if row is not None:
        if row.get("Protocol") != "Lighter":
            raise ValueError(f"Expected a Lighter vault at {TARGET}")
        _, rebuilt = create_lighter_pool_row(LIGHTER_ROBINHOOD_LLP_ACCOUNT_INDEX, "", None, 0, None, deployment=LIGHTER_ROBINHOOD)
        updates = {field: rebuilt[field] for field in FIELDS}
        updates["_flags"] = set(row.get("_flags") or ()) | rebuilt["_flags"]
        for field, value in updates.items():
            if row.get(field) != value:
                row[field] = value
                changed_fields += 1
    context.report(inspected_rows=int(row is not None), missing_specs=[] if row is not None else [TARGET.as_string_id()], updated_rows=int(changed_fields > 0), updated_fields=changed_fields)
    context.commit_metadata()

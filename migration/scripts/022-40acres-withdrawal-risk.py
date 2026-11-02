"""Persist visible withdrawal warnings for four reviewed 40acres vaults.

Purpose: Publish repayment-dependent withdrawal warnings without hiding vaults.
Scope: Exact Base Aerodrome, Optimism Velodrome and Avalanche Pharaoh and
Blackhole supply vaults below; update only _flags and _notes.
Branch: check-40acres-vault-liquidity-warning-notes
PR title: notes: explain 40acres withdrawal restrictions and loan exposure
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1712
Preserves: Unrelated flags and metadata, Hydrex, prices, leads, reader state
and timestamp caches. No historical price backfill is needed.
Expected: Four validated targets, at most eight updated fields; safe to retry.

LLM-generated operational migration; changes require human review.

Run after merge
--------------
Use the production checkout at ~/vault-scanner/web3-ethereum-defi and inspect
docker-compose.yml before maintenance. Both preview and apply require stopped
scanner services. The runner takes the lifecycle guard before the writer lock.

.. code-block:: shell

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
    docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt

The runner executes all pending migrations. Review their combined preview.
Confirm the printed commit matches migration.json. This script changes only
vault-metadata-db.pickle; the runner also writes its journal and backups.
After a successful normal publication cycle, verify the four exported vaults
remain visible, carry forty_acres_withdrawal_risk and contain the sourced notes.
Hydrex must retain its existing flags and notes. Migration success alone does
not publish the changes; the restarted scanner performs normal exports/uploads.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext

#: Fixed reviewed deployment inventory; Hydrex is intentionally excluded.
TARGETS = (
    VaultSpec(8453, "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5"),
    VaultSpec(10, "0x08dcdbf7bade91ccd42cb2a4ea8e5d199d285957"),
    VaultSpec(43114, "0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7"),
    VaultSpec(43114, "0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the four warnings after validating the complete inventory.

    Preserve unrelated flags and fields. Preview and apply use the runner's
    staged database, backups and atomic commit rather than direct file writes.

    :param context: Guarded migration context with the shared metadata store.
    :return: ``None`` after reporting changes and committing staged metadata.
    :raises ValueError: If a reviewed row is missing or has a different protocol.
    """
    database = context.metadata()
    for spec in TARGETS:
        if spec not in database.rows:
            raise ValueError(f"Reviewed 40acres vault row is missing: {spec}")
        if database.rows[spec].get("Protocol") != "40acres":
            raise ValueError(f"Unexpected protocol for reviewed 40acres vault: {spec}")

    changed_rows = 0
    updated_fields = 0
    for spec in TARGETS:
        row = database.rows[spec]
        updates = {
            "_flags": set(row.get("_flags") or ()) | get_vault_special_flags(spec.vault_address, "40acres"),
            "_notes": get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name="40acres"),
        }
        changed_fields = {key: value for key, value in updates.items() if row.get(key) != value}
        changed_rows += bool(changed_fields)
        updated_fields += len(changed_fields)
        row.update(changed_fields)

    context.report(inspected_rows=len(TARGETS), changed_rows=changed_rows, updated_fields=updated_fields)
    context.commit_metadata()

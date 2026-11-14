"""Persist the user-reported Peapods USDC 65 and 147 illiquidity warning.

Purpose: Refresh stored flags and notes so exports exclude the reviewed vaults.
Scope: Two exact Peapods deployments on Base below; update only _flags and _notes.
Branch: add-vault-note-peapods-usdc-illiquid
PR title: notes: mark Peapods USDC 65 and 147 on Base as illiquid
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1735
Preserves: Unrelated flags and metadata, prices, leads, reader state and
timestamp caches. No historical price backfill is needed.
Expected: Two validated targets, at most four updated fields; safe to retry.

LLM-generated operational migration; changes require human review.

Run after merge
---------------
Use the production checkout at ~/vault-scanner/web3-ethereum-defi and inspect
docker-compose.yml before maintenance. Both preview and apply require stopped
scanner services. The runner takes the lifecycle guard before the writer lock.

.. code-block:: shell

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
    docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt

Build the merged checkout with scripts/build-container.sh to stamp its git revision.
Use that image containing this migration. The runner executes all pending
migrations; review their combined preview. Confirm the printed commit matches
migration.json. The runner writes the metadata, journal and backups; this script
does not touch price history. After the normal publication cycle, verify that
both exported vaults carry illiquid and the note and are excluded from investable
rankings. Migration success alone does not publish the change; the restarted
scanner performs normal exports/uploads.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed Peapods USDC 65 and 147 deployments on Base.
TARGETS = (
    VaultSpec(8453, "0x2e5109aa82a4ebcc776e89abeb68f5d0b91ccea1"),
    VaultSpec(8453, "0x53ce611b5477997972dad83585a0ad5cb56f433b"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed warnings after validating both vault identities.

    Preserve unrelated flags and fields. The context owns preview staging,
    backups and atomic persistence; no network reads are needed.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing the proposed changes.
    :raises ValueError: If a reviewed row is missing or has another protocol.
    """
    database = context.metadata()
    for spec in TARGETS:
        if spec not in database.rows:
            raise ValueError(f"Reviewed illiquid vault row is missing: {spec}")
        if database.rows[spec].get("Protocol") != "Peapods":
            raise ValueError(f"Unexpected protocol for reviewed illiquid vault: {spec}")

    changed_rows = 0
    updated_fields = 0
    for spec in TARGETS:
        row = database.rows[spec]
        updates = {
            "_flags": set(row.get("_flags") or ()) | get_vault_special_flags(spec.vault_address, "Peapods"),
            "_notes": get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name="Peapods"),
        }
        changed_fields = {key: value for key, value in updates.items() if row.get(key) != value}
        row.update(changed_fields)
        changed_rows += bool(changed_fields)
        updated_fields += len(changed_fields)
    context.report(inspected_rows=len(TARGETS), changed_rows=changed_rows, updated_fields=updated_fields)
    context.commit_metadata()

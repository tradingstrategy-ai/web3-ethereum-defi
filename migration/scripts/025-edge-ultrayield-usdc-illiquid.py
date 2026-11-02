"""Persist the user-reported EDGE UltraYield USDC illiquidity warning.

Purpose: Refresh stored flags and notes so exports exclude the reviewed vault.
Scope: Exact Gearbox deployment on Monad below; update only _flags and _notes.
Branch: add-vault-note-illiquid-edge-ultrayield-usdc-5
PR title: notes: mark EDGE UltraYield USDC on Monad as illiquid
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1713
Preserves: Unrelated flags and metadata, prices, leads, reader state and
timestamp caches. No historical price backfill is needed.
Expected: One validated target, at most two updated fields; safe to retry.

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

Use an image containing this migration. The runner executes all pending
migrations; review their combined preview. Confirm the printed commit matches
migration.json. The runner writes the metadata, journal and backups; this script
does not touch price history. After the normal publication cycle, verify that
the exported vault carries illiquid and the note and is excluded from investable
rankings. Migration success alone does not publish the change; the restarted
scanner performs normal exports/uploads.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed Gearbox deployment on Monad, distinct from the Morpho vault on Base.
TARGET = VaultSpec(143, "0x6b343f7b797f1488aa48c49d540690f2b2c89751")


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed warning after validating the target's identity.

    Preserve unrelated flags and fields. The context owns preview staging,
    backups and atomic persistence; no network reads are needed.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing the proposed changes.
    :raises ValueError: If the reviewed row is missing or has another protocol.
    """
    database = context.metadata()
    if TARGET not in database.rows:
        raise ValueError(f"Reviewed illiquid vault row is missing: {TARGET}")
    row = database.rows[TARGET]
    if row.get("Protocol") != "Gearbox":
        raise ValueError(f"Unexpected protocol for reviewed illiquid vault: {TARGET}")

    updates = {
        "_flags": set(row.get("_flags") or ()) | get_vault_special_flags(TARGET.vault_address, "Gearbox"),
        "_notes": get_notes(TARGET.vault_address, chain_id=TARGET.chain_id, protocol_name="Gearbox"),
    }
    changed_fields = {key: value for key, value in updates.items() if row.get(key) != value}
    row.update(changed_fields)
    context.report(inspected_rows=1, changed_rows=int(bool(changed_fields)), updated_fields=len(changed_fields))
    context.commit_metadata()

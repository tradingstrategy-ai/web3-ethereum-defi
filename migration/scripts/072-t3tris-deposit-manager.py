# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Publish the asynchronous T3tris deposit manager capability.

Purpose: Refresh the persisted ``_deposit_manager`` capability now that T3tris
    vaults have a request, settle and claim manager.
Scope: Database-wide refresh of existing rows detected as ``t3tris_like``, on
    every chain. Only ``_deposit_manager`` changes.
Branch: check-t3tris-deposit-redeem-support
PR title: T3tris deposit and withdraw integration
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1774
Preserves: All other fields, non-T3tris rows, leads, prices, reader state and
    timestamp caches. No rows are created.
Expected: One inspected row per existing T3tris deployment; changed rows are
    those still carrying the old capability. Idempotent refresh.

LLM-generated operational migration; changes require human review.
No network reads or price backfill: the capability is static adapter metadata.

Run after merge. Include all pending migrations in the preview. Inspect the
Compose mounts and build the merged, stamped image with
scripts/build-container.sh first. From ~/vault-scanner/web3-ethereum-defi,
quiesce all other metadata writers::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner

Review preview counts before applying. Both modes acquire the lifetime guard;
apply then takes the writer lock. Use the same image and persistent mounts.
Check migration.json against the restarted image's GIT_VERSION_HASH.txt and
verify the exported ``deposit_manager`` field after the normal scanner
export/upload cycle. The runner neither scans prices nor publishes exports.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.t3tris.vault import T3trisVault
from eth_defi.vault_protocols.migration import MigrationContext


def migrate(context: MigrationContext) -> None:
    """Stage the T3tris capability on every existing T3tris row.

    The capability is static adapter metadata, so the adapter is built without
    a provider. The runner owns backups, preview and persistence.

    :param context: Guarded migration context and staged metadata database.
    :return: ``None`` after reporting and committing or previewing the refresh.
    """
    database = context.metadata()
    inspected = changed_rows = 0
    for spec, row in database.rows.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.t3tris_like not in detection.features:
            continue
        inspected += 1
        capability = T3trisVault(None, spec, features=detection.features).get_deposit_manager_capability().as_initial_public_schema()
        if row.get("_deposit_manager") != capability:
            row["_deposit_manager"] = capability
            changed_rows += 1

    context.report(
        inspected_rows=inspected,
        changed_rows=changed_rows,
        changed_fields=["_deposit_manager"] if changed_rows else [],
    )
    context.commit_metadata()

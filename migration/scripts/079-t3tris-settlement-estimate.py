# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Publish T3tris' average settlement interval as the settlement estimate.

Purpose: Refresh the persisted ``_withdrawal_period`` of T3tris rows with the
    non-binding ``estimated_settlement`` derived from the T3tris app API.
Scope: Database-wide refresh of existing rows detected as ``t3tris_like``, on
    every chain. Only ``_withdrawal_period`` changes; ``_lockup`` stays
    ``None`` because the scanner derives it from the binding ``max_period``.
Branch: t3tris-delay-estimates
PR title: feat: T3tris settlement delay estimates and settlement events
PR: pending
Preserves: All other fields, non-T3tris rows, leads, prices, reader state,
    timestamp caches and the T3tris metadata disk cache. No rows are created.
Expected: One inspected row per existing T3tris deployment, about a dozen.
    Vaults the app does not list, or without a usable interval, keep ``None``.
    Idempotent apart from the live interval changing between runs.

LLM-generated operational migration; changes require human review.
Network reads: one HTTPS request per T3tris vault to
``earn.t3tris.finance/api/vaults/{chainId}/{address}``. No RPC, no price
backfill. Detail responses are read fresh and are not written to the
metadata cache, whose existing files predate the new field.

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
verify the exported ``estimated_settlement`` field after the normal scanner
export/upload cycle. The runner neither scans prices nor publishes exports.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.t3tris.offchain_metadata import _fetch_vault_detail, _parse_vault_detail
from eth_defi.erc_4626.vault_protocol.t3tris.vault import create_t3tris_withdrawal_period
from eth_defi.vault_protocols.migration import MigrationContext


def migrate(context: MigrationContext) -> None:
    """Stage the T3tris settlement estimate on every existing T3tris row.

    Each vault's detail response is fetched fresh and parsed without the
    public metadata fetcher, whose process and disk caches could return data
    from before the interval was parsed. The runner owns backups, preview and
    persistence.

    :param context: Guarded migration context and staged metadata database.
    :return: ``None`` after reporting and committing or previewing the refresh.
    """
    database = context.metadata()
    inspected = changed_rows = with_estimate = 0
    for spec, row in database.rows.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.t3tris_like not in detection.features:
            continue
        inspected += 1
        raw = _fetch_vault_detail(spec.chain_id, spec.vault_address)
        seconds = _parse_vault_detail(raw)["average_settlement_seconds"] if raw is not None else None
        period = create_t3tris_withdrawal_period(seconds)
        with_estimate += period is not None
        if row.get("_withdrawal_period") != period:
            row["_withdrawal_period"] = period
            changed_rows += 1

    context.report(
        inspected_rows=inspected,
        changed_rows=changed_rows,
        rows_with_estimate=with_estimate,
        changed_fields=["_withdrawal_period"] if changed_rows else [],
    )
    context.commit_metadata()

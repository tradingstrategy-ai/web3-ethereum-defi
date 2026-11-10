# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Refresh RockawayX Liminal USDC descriptions and lending classification.

Purpose: Persist researched copy and the Morpho adapter's lending tag.
Scope: Chain 999, 0x83107e84f9c5eaaf31f6d2e2b06e0aa14121370e;
    only _description, _short_description and _strategy_tags.
Branch: check-stablecoin-vault-coverage-rockawayx
PR title: feat: add limUSD and describe RockawayX Liminal USDC
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1755
Preserves: All other metadata, unrelated rows, prices, leads, reader state
    and timestamp caches. No rows are created.
Expected: At most one existing row and three changed fields; missing rows reported.
LLM-generated operational migration; changes require human review.
No network reads or historical price backfill: this is a display-only refresh.

Run after merge. Inspect docker-compose.yml, build the merged stamped image,
and quiesce competing metadata writers before preview and apply. From
~/vault-scanner/web3-ethereum-defi::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner

Review preview counts before apply. Preserve the same image and persistent
mounts. The runner acquires the lifetime guard before the writer lock.
The normal scanner export cycle publishes the refreshed fields afterwards.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import ROCKAWAYX_LIMINAL_VAULT_METADATA
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed HyperEVM deployment; same-address rows on other chains are excluded.
TARGET = VaultSpec(999, "0x83107e84f9c5eaaf31f6d2e2b06e0aa14121370e")


def migrate(context: MigrationContext) -> None:
    """Refresh the reviewed copy and tags after validating protocol evidence.

    Resolve classifications before staging any changes so conflicting records
    fail without partially updating the shared staged database. The context
    owns preview, backups and atomic persistence.

    :param context: Guarded attempt and staged metadata database.
    :return: None after reporting and committing or previewing the repair.
    :raises ValueError: If protocol evidence or resolved tags conflict.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, changed_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return
    detection = row.get("_detection_data")
    if row.get("Protocol") != "Morpho" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.morpho_v2_like not in detection.features:
        raise ValueError(f"RockawayX Liminal target {TARGET} is not classified as Morpho V2")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={TARGET: row}))
    if skipped or any(update.new_tags != frozenset({StrategyTag.lending}) for update in updates):
        raise ValueError(f"Could not resolve the reviewed lending classification for {TARGET}")
    metadata = ROCKAWAYX_LIMINAL_VAULT_METADATA[TARGET.chain_id, TARGET.vault_address]
    fields = {
        "_description": normalise_vault_description(metadata.description),
        "_short_description": normalise_vault_short_description(metadata.short_description),
    }
    for update in updates:
        fields["_strategy_tags"] = set(update.new_tags)
    changes = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(changes)
    context.report(inspected_rows=1, changed_rows=int(bool(changes)), missing_specs=[], updated_fields=len(changes), legacy_tag_rows=invalid)
    context.commit_metadata()

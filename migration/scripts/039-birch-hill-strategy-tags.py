# ruff: noqa: N999
"""Refresh the Birch Hill Flagship's persisted strategy tags.

Purpose: Classify the documented USDC allocation to real-world asset lending.
Scope: Only _strategy_tags for Base 8453:0xa5afd3a598ef000a5edd96ed65929ac7a6feea39.
Branch: analyse-birch-hill-vault-protocol
PR title: feat: publish Birch Hill vault descriptions and correct deposit permissions
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1738
Preserves: All other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most one existing row and one changed field; missing row reported.

LLM-generated operational migration; changes require human review.
No RPC reads, discovery or historical price backfill are needed. Run after merge
with 037 and 038 using the production commands in 037; stop scanner and competing
writers before preview and apply, and preserve lifetime-guard-before-writer-lock
order. Normal exports publish the corrected tags and curator attribution.
"""

from eth_defi.erc_4626.vault_protocol.yearn.vault import BIRCH_HILL_RWA_USDC
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates


def migrate(context: MigrationContext) -> None:
    """Refresh the reviewed existing row through the maintained tag resolver.

    Validate identity and resolver availability before staging the update.
    The runner owns preview, backups and persistence.

    :param context: Guarded migration and staged database.
    :return: ``None`` after reporting and committing the proposed change.
    :raises ValueError: If protocol attribution or detection metadata conflicts.
    """
    database = context.metadata()
    spec = BIRCH_HILL_RWA_USDC
    row = database.rows.get(spec)
    if row is None:
        context.report(inspected_rows=0, changed_rows=0, updated_fields=0, missing_specs=[spec.as_string_id()])
        context.commit_metadata()
        return
    if row.get("Protocol") not in {"Yearn", "ERC-4626"}:
        raise ValueError(f"Unexpected protocol for Birch Hill Flagship: {row.get('Protocol')}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows={spec: row}))
    if skipped or any(update.new_tags is None for update in updates):
        raise ValueError(f"Cannot resolve Birch Hill Flagship strategy tags: {spec}")
    for update in updates:
        row["_strategy_tags"] = set(update.new_tags)
    context.report(inspected_rows=1, changed_rows=len(updates), updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()

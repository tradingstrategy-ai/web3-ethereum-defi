"""Refresh maintained strategy tags for all classified Lagoon deployments.

Purpose: Apply the 10 November 2026 Lagoon strategy review to persisted rows.
Scope: Database-wide Lagoon rows with a maintained chain/address classification;
only _strategy_tags changes. Unclassified vaults retain their existing tags.
Branch: check-lagoon-vault-descriptions-strategy-tags
PR title: fix: refresh Lagoon metadata and strategy tags
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1756
Preserves: Text, flags, curator assignments, other protocols, prices and reader state.
Expected: At most one changed field per classified existing Lagoon row; no inserts.
No network reads, price backfill or eligibility changes are required.

LLM-generated operational migration; changes require human review.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.tags import lookup_lagoon_strategy_tags
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates, resolve_strategy_tags


def migrate(context: MigrationContext) -> None:
    """Stage classified Lagoon strategy tags through the shared repair helper.

    Validate adapter identity before resolving tags so an incompatible row
    cannot silently receive another protocol's classification. Leave missing
    strategy information untouched; the runner owns locking and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing changes.
    :raises ValueError: If a classified Lagoon row cannot resolve safely.
    """
    database = context.metadata()
    lagoon = {spec: row for spec, row in database.rows.items() if row.get("Protocol") in {"Lagoon", "Lagoon Finance"}}
    classified = {spec: row for spec, row in lagoon.items() if lookup_lagoon_strategy_tags(spec.chain_id, spec.vault_address) is not None}
    for spec, row in classified.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.lagoon_like not in detection.features:
            raise ValueError(f"Lagoon target {spec} lacks Lagoon adapter detection")
        resolved = resolve_strategy_tags(spec, row)
        if resolved is None or "Lagoon" not in resolved[1] or not resolved[1].endswith("chain-aware tag resolver"):
            raise ValueError(f"Lagoon target {spec} resolves through an incompatible adapter")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=classified))
    if skipped:
        raise ValueError("Classified Lagoon strategy tags could not be resolved through the Lagoon adapter")
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
    context.report(inspected_rows=len(lagoon), classified_rows=len(classified), unresolved_rows=len(lagoon) - len(classified), updated_fields=len(updates), legacy_tag_rows=invalid)
    context.commit_metadata()

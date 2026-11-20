"""Publish podcast coverage for reviewed vault deployments.

Purpose: Make vaults featured in Trading Strategy interviews discoverable.
Scope: Only _strategy_tags for the deployment inventory from episodes 1 to 16.
Branch: add-podcast-available-strategy-tag
PR title: feat: tag vaults covered by the Trading Strategy podcast
PR: Pending
Preserves: Existing recognised strategy tags, unrelated metadata and historical data.
Expected: Up to 25 changed rows. Report missing deployments without creating rows.

LLM-generated operational migration; changes require human review.
Run after merge through vault-migrate with the scanner stopped.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.podcast_tags import PODCAST_VAULTS_EPISODES_1_TO_16
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import normalise_persisted_strategy_tags


def migrate(context: MigrationContext) -> None:
    """Add the editorial tag to the reviewed vaults' saved classifications.

    The existing strategy tags may have been populated by many different
    adapters. Add coverage directly so that the repair also works when there
    is no offline resolver for a protocol's investment strategy.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the refresh.
    """
    database = context.metadata()
    targets = tuple(VaultSpec(chain_id, address) for chain_id, address in sorted(PODCAST_VAULTS_EPISODES_1_TO_16))
    missing = []
    updated_fields = 0
    legacy_tag_rows = 0
    for spec in targets:
        row = database.rows.get(spec)
        if row is None:
            missing.append(spec.as_string_id())
            continue

        tags, canonical = normalise_persisted_strategy_tags(row.get("_strategy_tags"))
        legacy_tag_rows += not canonical
        if not canonical or StrategyTag.podcast_available not in (tags or ()):
            row["_strategy_tags"] = (tags or set()) | {StrategyTag.podcast_available}
            updated_fields += 1

    context.report(inspected_rows=len(targets) - len(missing), missing_specs=missing, updated_fields=updated_fields, legacy_tag_rows=legacy_tag_rows)
    context.commit_metadata()

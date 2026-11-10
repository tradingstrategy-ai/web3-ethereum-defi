# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Refresh existing reviewed K3 wrappers without changing price history.

Purpose: Apply K3 identity, researched descriptions, strategy tags and safe execution metadata.
Scope: All five reviewed K3 wrappers; missing rows are reported.
Preserves: Unrelated fields, notes, flags, prices, leads, reader state and caches.
Expected: Repeated transformations produce identical rows.
Branch: check-vault-from-k3-capital-post
PR title: feat: add K3 vault readers and offchain metadata
PR: Not opened.
LLM-generated operational migration; changes require human review.
Run after merge: stop the looped scanner and run the numbered migration runner,
then export and publish metadata. See .claude/docs/migration-scripts.md.
No backfill: generic readers already read the same totalAssets/share conversions.
This onboarding migration has not been applied; it includes the cirBTC launch-thread
and cbBTC product-documentation refinements, including risk_premium_harvest,
for the same five-vault scope.
"""

from dataclasses import replace

from eth_defi.erc_4626.classification import K3_HARDCODED_PROTOCOLS
from eth_defi.erc_4626.vault_protocol.k3.constants import K3_VAULTS
from eth_defi.erc_4626.vault_protocol.k3.tags import STRATEGY_TAGS
from eth_defi.vault.base import VaultSpec, WithdrawalDelayType, WithdrawalPeriod, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import lookup_strategy_tags
from eth_defi.vault_protocols.migration import MigrationContext


def migrate(context: MigrationContext) -> None:
    """Refresh catalogue fields using maintained identity and copy.

    The runner owns persistence and preview. Unknown admission is retained
    explicitly, without guessing from the public app or funding conditions.

    :param context: Guarded migration context.
    :return: ``None`` after reporting and committing the staged repair.
    """
    database = context.metadata()
    missing = []
    for chain, address in sorted(K3_VAULTS):
        spec = VaultSpec(chain, address)
        row = database.rows.get(spec)
        if row is None:
            missing.append(spec.as_string_id())
            continue
        metadata = get_handwritten_vault_metadata(chain, address)
        assert metadata is not None
        features = set(row.get("features", set())) | K3_HARDCODED_PROTOCOLS[address]
        row.update({"Protocol": "K3", "features": features, "Features": ", ".join(sorted(f.name for f in features)), "_description": normalise_vault_description(metadata.description), "_short_description": normalise_vault_short_description(metadata.short_description), "_deposit_manager": None, "Link": "https://app.k3.capital/", "_withdrawal_period": WithdrawalPeriod(None, None, WithdrawalDelayType.delay), "_deposits_open": None, "_deposit_closed_reason": None, "_redemption_closed_reason": None, "_deposit_permission": "unknown", "_strategy_tags": lookup_strategy_tags(STRATEGY_TAGS, address)})
        if row.get("_detection_data") is not None:
            row["_detection_data"] = replace(row["_detection_data"], features=features)
    context.report(missing_specs=missing)
    context.commit_metadata()

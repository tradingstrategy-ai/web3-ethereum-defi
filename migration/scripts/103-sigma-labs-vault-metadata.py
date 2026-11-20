# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Refresh persisted descriptions and strategy tags for Sigma Labs vaults.

Purpose: Publish researched vault strategies for existing scanner records.
Scope: Refresh both description fields and strategy tags for seven exact deployments.
Branch: add-curator-sigmalabs
PR title: feat: complete Sigma Labs curator and vault metadata
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1804
Preserves: Curator fields, eligibility, fees, other metadata and price history.
Expected: Up to seven changed rows and 21 changed fields. Report missing rows.

LLM-generated operational migration. Changes require human review.
Run after merge through vault-migrate with the scanner stopped.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import EVM_ADAPTER_FEATURE_PRIORITY, NATIVE_STRATEGY_TAG_RESOLVERS, collect_strategy_tag_updates

#: Fixed deployment scope and expected adapter for each reviewed vault.
TARGETS = {
    VaultSpec(1, "0x2746f31096f23670caf4043f8b30d8d02405a257"): ERC4626Feature.lagoon_like,
    VaultSpec(1, "0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89"): ERC4626Feature.lagoon_like,
    VaultSpec(1, "0x8595f5ee036e1f7c4f3ca66327ba5f38c21cb7fb"): ERC4626Feature.lagoon_like,
    VaultSpec(8453, "0x79238962903c62865f319f660177e75652ee4780"): ERC4626Feature.lagoon_like,
    VaultSpec(42161, "0x0f30d4cc24024e51bee5e7805bc7f3e6a88ea59f"): ERC4626Feature.lagoon_like,
    VaultSpec(1, "0x7d2bd5eb7066957c135585f0f055443451baadad"): ERC4626Feature.lagoon_like,
    VaultSpec(1, "0x6b833881a9f083ad2ccacf8c10abc0dc151cbf32"): ERC4626Feature.morpho_v2_like,
}


def migrate(context: MigrationContext) -> None:
    """Stage the current maintained metadata for the reviewed deployments.

    Validate every present row before staging changes. A missing row will use
    the maintained accessors when the scanner discovers it.

    :param context: Guarded migration attempt and staged metadata database.
    :return: None after reporting the changes and committing or previewing them.
    :raises ValueError: If a present row has incompatible detection or the
        maintained metadata cannot be resolved.
    """
    database = context.metadata()
    rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    descriptions = {}
    for spec, row in rows.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection):
            raise ValueError(f"{spec.as_string_id()} lacks vault detection")
        adapter = next((feature for feature in EVM_ADAPTER_FEATURE_PRIORITY if feature in detection.features), None)
        if adapter != TARGETS[spec] or any(feature in detection.features for feature in NATIVE_STRATEGY_TAG_RESOLVERS):
            raise ValueError(f"{spec.as_string_id()} has an incompatible vault adapter")
        metadata = get_handwritten_vault_metadata(spec.chain_id, spec.vault_address)
        if metadata is None or not metadata.description or not metadata.short_description:
            raise ValueError(f"{spec.as_string_id()} lacks maintained descriptions")
        descriptions[spec] = {
            "_description": normalise_vault_description(metadata.description),
            "_short_description": normalise_vault_short_description(metadata.short_description),
        }

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve strategy tags for all reviewed Sigma Labs vaults"
        raise ValueError(message)

    updated_fields = len(updates)
    for update in updates:
        rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    for spec, copy in descriptions.items():
        row = rows[spec]
        updated_fields += sum(row.get(field) != value for field, value in copy.items())
        row.update(copy)

    context.report(inspected_rows=len(rows), missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in rows], updated_fields=updated_fields, legacy_tag_rows=invalid)
    context.commit_metadata()

"""Refresh the reviewed curator-selected yield strategy tags.

Purpose: Include documented human investment selection alongside yield mechanisms.
Scope: Nine frozen deployments below; only _strategy_tags changes.
Branch: add-lagoon-curator-records
PR title: feat: add Lagoon curator records and reviewed vault descriptions
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1757
Preserves: Descriptions, curator attribution, fees, flags, eligibility, unrelated
    rows, prices, leads, reader state and timestamp caches.
Expected: At most nine existing rows and nine changed fields; missing rows reported.
No network reads or new discovery. The existing price-gap investigation remains
in docs/protocol-research/lagoon-curator-onboarding-2026-11-10.md; this repair
changes no scan eligibility or historical coverage.

LLM-generated operational migration; changes require human review.
Run after merge using the stopped-writer preview/apply procedure documented in
061-lagoon-curator-descriptions.py, reviewing the entire pending batch.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import EVM_ADAPTER_FEATURE_PRIORITY, NATIVE_STRATEGY_TAG_RESOLVERS, collect_strategy_tag_updates

#: Exact deployments and their permitted adapter identities; never expand at runtime.
TARGETS = {
    VaultSpec(1, "0x7d2c2f54792ad72cb834d298f542145b06b703cb"): {ERC4626Feature.lagoon_like},
    VaultSpec(8453, "0x8092ca384d44260ea4feaf7457b629b8dc6f88f0"): {ERC4626Feature.lagoon_like},
    VaultSpec(8453, "0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523"): {ERC4626Feature.lagoon_like},
    VaultSpec(8453, "0xb09f761cb13baca8ec087ac476647361b6314f98"): {ERC4626Feature.lagoon_like},
    VaultSpec(8453, "0xbe7db44f4ce20dac83b578b94fd35087f66e9754"): {ERC4626Feature.lagoon_like},
    VaultSpec(8453, "0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550"): {ERC4626Feature.lagoon_like},
    VaultSpec(1, "0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac"): {ERC4626Feature.morpho_like, ERC4626Feature.morpho_v2_like},
    VaultSpec(1, "0x29ca54dd3ae9e43abb10880f5e08da439bf25f99"): {ERC4626Feature.morpho_like, ERC4626Feature.morpho_v2_like},
    VaultSpec(8453, "0xcda5dbf58a016158ecd4a226109752cc3a627ec8"): {ERC4626Feature.morpho_like, ERC4626Feature.morpho_v2_like},
}


def migrate(context: MigrationContext) -> None:
    """Validate the exact inventory before staging the shared tag refresh.

    Resolve through the same protocol mapping used by the scanner. Incompatible
    detection or missing classifications fail before any row is changed.
    Missing vaults are reported and never inserted. The runner owns all writes.

    :param context: Guarded migration attempt and staged metadata database.
    :return: None after reporting and committing or previewing the changes.
    :raises ValueError: If an existing target cannot be resolved safely.
    """
    database = context.metadata()
    targets = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in targets.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection):
            raise ValueError(f"Missing adapter detection for curator strategy target {spec}")
        adapter = next((feature for feature in EVM_ADAPTER_FEATURE_PRIORITY if feature in detection.features), None)
        protocols = {"Morpho"} if ERC4626Feature.morpho_like in TARGETS[spec] else {"Lagoon", "Lagoon Finance"}
        if row.get("Protocol") not in protocols or adapter not in TARGETS[spec] or any(feature in detection.features for feature in NATIVE_STRATEGY_TAG_RESOLVERS):
            raise ValueError(f"Incompatible adapter for curator strategy target {spec}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=targets))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Curator strategy targets could not resolve maintained tags"
        raise ValueError(message)
    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(inspected_rows=len(targets), updated_fields=len(updates), legacy_tag_rows=invalid, missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in targets])
    context.commit_metadata()

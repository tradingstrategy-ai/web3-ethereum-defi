# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Populate Agent RockSteady's strategy description and source-linked notes.

Purpose: Replace the old launch summary and missing strategy description with maintained NuWealth strategy copy.
Scope: Only _description, _short_description and _notes for Hypercore 9999:0xd321ad79041bdad719ba5ff42fe950e1977df439.
Branch: notes/agent-rocksteady-strategy
PR title: notes: describe Agent RockSteady's strategy and research history
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1706
Preserves: Names, links, fees, flags, risk, strategy tags, unrelated rows, leads, prices, reader state and timestamp caches.
Expected: At most one existing row and three changed fields; absent targets are reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. No network access or historical backfill is required.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.flag import get_notes
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed deployment frozen on 2026-10-31.
TARGET = VaultSpec(9999, "0xd321ad79041bdad719ba5ff42fe950e1977df439")


def migrate(context: MigrationContext) -> None:
    """Stage the same copy used by the native Hyperliquid exporter.

    Validate the exact deployment's native protocol evidence before staging
    changes. The migration context owns preview, backups and persistence.

    :param context: Guarded migration context and shared staged database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target is not classified as a Hyperliquid vault.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return
    detection = row.get("_detection_data")
    if row.get("Protocol") != "Hyperliquid" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.hypercore_native not in detection.features:
        raise ValueError(f"Agent RockSteady target {TARGET} is not classified as a Hyperliquid native vault")
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, TARGET.vault_address)
    assert metadata is not None, "Agent RockSteady maintained metadata is missing"
    fields = {
        "_description": normalise_vault_description(metadata.description),
        "_short_description": normalise_vault_short_description(metadata.short_description),
        "_notes": get_notes(TARGET.vault_address, chain_id=TARGET.chain_id, protocol_name="Hyperliquid"),
    }
    updates = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(updates)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates))
    context.commit_metadata()

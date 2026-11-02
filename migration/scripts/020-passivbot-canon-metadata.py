# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Populate Passivbot Canon's maintained strategy copy and classifications.

Purpose: Replace the template-only summary and missing description with researched PassivBot copy and strategy tags.
Scope: Only _description, _short_description and _strategy_tags for Hypercore 9999:0x490af7d4a048a81db0f677517ed6373565b42349.
Branch: notes/passivbot-canon-metadata
PR title: notes: document PassivBot curator and reference vault strategy
PR: Not opened.
Preserves: Names, links, notes, fees, flags, risk, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: At most one existing row and three changed fields; absent targets are reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. No network access or historical backfill is required.
Curator attribution is resolved during export from the maintained address mapping.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.hyperliquid.tags import get_strategy_tags
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Exact reference deployment reviewed on 2026-11-01.
TARGET = VaultSpec(9999, "0x490af7d4a048a81db0f677517ed6373565b42349")


def migrate(context: MigrationContext) -> None:
    """Stage the copy and tags used by the native Hyperliquid exporter.

    Validate the deployment's native protocol evidence before staging changes.
    The context owns preview, backups and atomic metadata persistence; no live
    provider requests or historical scanning are needed for this display repair.

    :param context: Guarded migration context and shared staged database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target is not classified as a Hyperliquid native vault.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return
    detection = row.get("_detection_data")
    if row.get("Protocol") != "Hyperliquid" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.hypercore_native not in detection.features:
        raise ValueError(f"Passivbot Canon target {TARGET} is not classified as a Hyperliquid native vault")
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, TARGET.vault_address)
    assert metadata is not None, "Passivbot Canon maintained metadata is missing"
    fields = {
        "_description": normalise_vault_description(metadata.description),
        "_short_description": normalise_vault_short_description(metadata.short_description),
        "_strategy_tags": get_strategy_tags(TARGET.vault_address),
    }
    updates = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(updates)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates))
    context.commit_metadata()

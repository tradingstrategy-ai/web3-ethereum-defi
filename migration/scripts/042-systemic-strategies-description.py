"""Populate Systemic Strategies L/S Grids' maintained descriptions.

Purpose: Replace promotional provider copy with researched strategy descriptions.
Scope: Only _description and _short_description for Hypercore 9999:0x07fd993f0fa3a185f7207adccd29f7a87404689d.
Branch: add-hft-high-win-rate-tags
PR title: notes: describe Systemic Strategies L/S Grids and classify its trading strategy
PR: Not opened.
Preserves: Names, links, fees, flags, notes, strategy tags, unrelated rows, leads, prices, reader state and timestamp caches.
Expected: At most one existing row and two changed fields; missing target reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
No network access or historical backfill is required. Run after merge using
the production reminder in docs/README-vault-description-formatting.md.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed native Hyperliquid vault, frozen on 2026-11-06.
TARGET = VaultSpec(9999, "0x07fd993f0fa3a185f7207adccd29f7a87404689d")


def migrate(context: MigrationContext) -> None:
    """Stage the same descriptions used by future native vault exports.

    Validate the exact deployment's native protocol evidence before staging
    changes. The context owns preview, backups and persistence.

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
        raise ValueError(f"Systemic Strategies target {TARGET} is not classified as a Hyperliquid native vault")
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, TARGET.vault_address)
    assert metadata is not None, "Systemic Strategies maintained metadata is missing"
    fields = {
        "_description": normalise_vault_description(metadata.description),
        "_short_description": normalise_vault_short_description(metadata.short_description),
    }
    updates = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(updates)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates))
    context.commit_metadata()

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Refresh NuWealth's live-record-only vault description and notes.

Purpose: Remove historical test figures and use the operator-supplied public live-record start date of 4 February 2026.
Scope: Only _description and _notes for Hypercore 9999:0xd321ad79041bdad719ba5ff42fe950e1977df439.
Branch: notes/nuwealth-live-record-copy
PR title: notes: update NuWealth.ai to live-record-only copy
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1718
Preserves: Summary, names, links, discovery timestamps, fees, flags, risk, strategy tags, unrelated rows, leads, prices, reader state and timestamp caches.
Expected: At most one existing row and two changed fields; absent/current targets are reported without rewriting metadata.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. No network access or price backfill is required.
The displayed start date changes no price boundary. Migration 017 remains unchanged.
"""

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec, normalise_vault_description
from eth_defi.vault.flag import get_notes
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed native deployment frozen on 2026-11-03.
TARGET = VaultSpec(9999, "0xd321ad79041bdad719ba5ff42fe950e1977df439")


def migrate(context: MigrationContext) -> None:
    """Refresh two persisted fields through the maintained export resolvers.

    An earlier successful copy migration must remain untouched and skipped.
    Validate native protocol evidence before staging this later correction;
    the context owns preview, backups and persistence.

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
        "_notes": get_notes(TARGET.vault_address, chain_id=TARGET.chain_id, protocol_name="Hyperliquid", include_defaults=False),
    }
    updates = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(updates)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates))
    context.commit_metadata()

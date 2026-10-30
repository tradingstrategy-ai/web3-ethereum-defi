"""Backfill the Markdown "About the vault" text for Neutral Trade Autopilot.

Purpose: Replace the single dense paragraph with Markdown split into short paragraphs, with subheadings, bold key facts and inline source links.
Scope: Ethereum and Monad Neutral Trade Autopilot deployments; `_description` only. Summaries, notes, links and tags are unchanged.
Branch: neutral-trade-followups
PR title: fix: format vault descriptions as Markdown paragraphs
PR: Not yet opened
Preserves: Names, summaries, notes, links, fees, flags, tags, manager names, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: Up to two existing rows and two changed fields; missing rows reported, never created. Repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. No network access is required.
"""

from eth_defi.vault.base import VaultSpec, normalise_vault_description
from eth_defi.vault.handwritten_metadata import NEUTRAL_AUTOPILOT_VAULT_METADATA
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed deployment inventory frozen on 2026-10-30.
TARGETS = (
    VaultSpec(1, "0x909dadbca7955614a455d9e7447ad4adb4902c8e"),
    VaultSpec(143, "0xaabab7598be3c4fe58c593e73c2f5934b73b573e"),
)


def migrate(context: MigrationContext) -> None:
    """Refresh the Markdown description for the frozen Autopilot inventory.

    Migration 011 persisted these descriptions while the field was still
    single-line text. Validate every existing target before staging changes.
    The same maintained constants supply the Accountable adapter, so future
    scans keep the Markdown text; the runner owns preview, backups and
    persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting the changes and committing or previewing them.
    :raises ValueError: If an existing target is not classified as Accountable, or the maintained text is not normalised Markdown.
    """
    database = context.metadata()
    missing = [spec.as_string_id() for spec in TARGETS if spec not in database.rows]
    updates = {}
    for spec in TARGETS:
        row = database.rows.get(spec)
        if row is None:
            continue
        if row.get("Protocol") != "Accountable":
            raise ValueError(f"Neutral Trade Autopilot target {spec} has unexpected protocol {row.get('Protocol')!r}")
        description = NEUTRAL_AUTOPILOT_VAULT_METADATA[spec.chain_id, spec.vault_address].description
        if description != normalise_vault_description(description):
            raise ValueError(f"Neutral Trade Autopilot description for {spec} is not normalised Markdown")
        if row.get("_description") != description:
            updates[spec] = description

    for spec, description in updates.items():
        database.rows[spec]["_description"] = description
    context.report(inspected_rows=len(TARGETS) - len(missing), missing_specs=missing, updated_fields=len(updates))
    context.commit_metadata()

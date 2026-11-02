"""Separate the Neutral Trade Autopilot strategy description from its risk notes.

Purpose: The "Notes" box repeated the description in one dense paragraph. Refresh the description to cover only the strategy, and the note to cover only centralised risks as Markdown with subheadings and bullet points.
Scope: Ethereum and Monad Neutral Trade Autopilot deployments; `_description` and `_notes` only.
Branch: neutral-trade-notes-markdown
PR title: notes: split Neutral Trade Autopilot strategy and risk notes
PR: Not yet opened
Preserves: Names, summaries, links, fees, flags, tags, manager names, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: Up to two existing rows and four changed fields; missing rows reported, never created. Repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. No network access is required.
"""

from eth_defi.vault.base import VaultSpec, normalise_vault_description
from eth_defi.vault.handwritten_metadata import NEUTRAL_AUTOPILOT_NOTE, NEUTRAL_AUTOPILOT_VAULT_METADATA
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed deployment inventory frozen on 2026-10-31.
TARGETS = (
    VaultSpec(1, "0x909dadbca7955614a455d9e7447ad4adb4902c8e"),
    VaultSpec(143, "0xaabab7598be3c4fe58c593e73c2f5934b73b573e"),
)


def migrate(context: MigrationContext) -> None:
    """Refresh the description and note of the frozen Autopilot inventory.

    Exports already resolve the current manual note, but the persisted row
    keeps the text written by earlier migrations. Validate every existing
    target before staging changes. The same maintained constants supply the
    Accountable adapter and the manual note table, so future scans keep the
    refreshed text.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting the changes and committing or previewing them.
    :raises ValueError: If a target is not an Accountable vault or the maintained text is not normalised Markdown.
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
        fields = {"_description": description, "_notes": NEUTRAL_AUTOPILOT_NOTE}
        updates[spec] = {field: value for field, value in fields.items() if row.get(field) != value}

    for spec, fields in updates.items():
        database.rows[spec].update(fields)
    context.report(inspected_rows=len(TARGETS) - len(missing), missing_specs=missing, updated_fields=sum(len(fields) for fields in updates.values()))
    context.commit_metadata()

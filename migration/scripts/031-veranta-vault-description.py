# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Populate the individual avUSDC vault's missing description fields.

Purpose: Publish reviewed vault copy independently of protocol metadata.
Scope: Only _description and _short_description for Base 8453:0x944766f715b51967e56afde5f0aa76ceacc9e7f9.
Branch: fix/veranta-vault-description
PR title: fix: publish Veranta vault descriptions
PR: Not yet opened; record the PR URL in this branch before merge.
Preserves: Names, links, fees, flags, notes, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: At most one existing row and two changed fields; missing target reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. No network access or historical backfill is required.
Run after merge using the production reminder in docs/README-vault-description-formatting.md.
"""

from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed unified avUSDC deployment, frozen on 2026-11-05.
TARGET = VaultSpec(8453, "0x944766f715b51967e56afde5f0aa76ceacc9e7f9")


def migrate(context: MigrationContext) -> None:
    """Stage the same reviewed descriptions that future scanner reads use.

    Validate protocol attribution before changing the existing target. The
    runner owns staging, preview, locks and persistence; this script neither
    discovers vaults nor requests blockchain state.

    :param context: Guarded migration context and shared staged database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the existing target has conflicting protocol attribution.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return
    if row.get("Protocol") != "Veranta":
        raise ValueError(f"Veranta description target {TARGET} has unexpected protocol {row.get('Protocol')!r}")
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, TARGET.vault_address)
    assert metadata is not None, "Veranta maintained metadata is missing"
    fields = {
        "_description": normalise_vault_description(metadata.description),
        "_short_description": normalise_vault_short_description(metadata.short_description),
    }
    updates = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(updates)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates))
    context.commit_metadata()

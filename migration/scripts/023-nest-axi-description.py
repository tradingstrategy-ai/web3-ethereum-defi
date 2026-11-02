# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Replace Nest AXI's marketing copy with a plain-language explanation.

Purpose: Refresh the listing summary and full description from reviewed copy.
Scope: Only _description and _short_description for Plume 98866:0xe154b281bd9ef3bd94df2364799d0b22b1d4ebd0.
Branch: explain-nest-axi-vault-description-override
PR title: feat: clarify Nest AXI vault descriptions
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1715
Preserves: Names, links, fees, flags, notes, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: At most one existing row and two changed fields; missing target reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. No network access or historical backfill is required.
Run after merge using the production reminder in docs/README-vault-description-formatting.md.
"""

from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed Plume vault entrypoint, frozen on 2026-11-02.
TARGET = VaultSpec(98866, "0xe154b281bd9ef3bd94df2364799d0b22b1d4ebd0")


def migrate(context: MigrationContext) -> None:
    """Stage the descriptions used by the Nest adapter for the reviewed vault.

    Validate the existing classification before editing display fields. The
    context owns preview, backups and persistence; a missing target is reported
    without discovery, insertion or provider requests.

    :param context: Guarded migration context and shared staged database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target is not classified as Nest.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return
    if row.get("Protocol") != "Nest":
        raise ValueError(f"Nest AXI target {TARGET} has unexpected protocol {row.get('Protocol')!r}")
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, TARGET.vault_address)
    assert metadata is not None, "Nest AXI maintained metadata is missing"
    fields = {
        "_description": normalise_vault_description(metadata.description),
        "_short_description": normalise_vault_short_description(metadata.short_description),
    }
    updates = {field: value for field, value in fields.items() if row.get(field) != value}
    row.update(updates)
    context.report(inspected_rows=1, missing_specs=[], updated_fields=len(updates))
    context.commit_metadata()

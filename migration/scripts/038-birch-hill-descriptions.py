# ruff: noqa: N999
"""Publish researched descriptions for both documented Birch Hill vaults.

Purpose: Populate individual vault summaries and full Markdown descriptions.
Scope: Two frozen Base addresses below; _description and _short_description only.
Branch: analyse-birch-hill-vault-protocol
PR title: feat: publish Birch Hill vault descriptions and correct deposit permissions
PR: Not opened; add URL before merge.
Preserves: All other metadata, prices, leads, reader state and timestamp caches.
Expected: At most two existing rows and four fields; missing rows reported.

LLM-generated migration; requires human review. This display-only refresh does
not discover vaults or backfill prices. Run after merge using the production
reminder in docs/README-vault-description-formatting.md. Inspect Compose mounts,
build the stamped merged image, and stop scanner and competing writers before
both preview and apply. The runner takes the lifetime guard before the writer
lock. Normal scanner exports/uploads publish the refreshed text after apply.
"""

from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed deployments; keep migration scope independent of future catalogue additions.
TARGETS = {
    VaultSpec(8453, "0xb038cf3e233b78225f6d350ac8319bec8457db4a"): {"Morpho"},
    VaultSpec(8453, "0xa5afd3a598ef000a5edd96ed65929ac7a6feea39"): {"Yearn", "ERC-4626"},
}


def migrate(context: MigrationContext) -> None:
    """Stage the adapter descriptions after validating every existing target.

    Missing rows are reported without inserting incomplete vault records.
    The runner owns preview staging, atomic writes and backups.

    :param context: Guarded migration context and staged metadata.
    :return: None after reporting and committing the proposed descriptions.
    :raises ValueError: If a target has conflicting protocol attribution.
    """
    database = context.metadata()
    for spec, protocols in TARGETS.items():
        row = database.rows.get(spec)
        if row is not None and row.get("Protocol") not in protocols:
            raise ValueError(f"Unexpected protocol for Birch Hill description target {spec}: {row.get('Protocol')}")
    inspected = changed_rows = updated_fields = 0
    for spec in TARGETS:
        row = database.rows.get(spec)
        if row is None:
            continue
        metadata = get_handwritten_vault_metadata(spec.chain_id, spec.vault_address)
        assert metadata is not None
        fields = {"_description": normalise_vault_description(metadata.description), "_short_description": normalise_vault_short_description(metadata.short_description)}
        updates = {key: value for key, value in fields.items() if row.get(key) != value}
        row.update(updates)
        inspected += 1
        changed_rows += bool(updates)
        updated_fields += len(updates)
    context.report(inspected_rows=inspected, changed_rows=changed_rows, updated_fields=updated_fields, missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in database.rows])
    context.commit_metadata()

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Refresh D2 summaries and remove GlobalMacro's repeated chart figures.

Purpose: Populate researched summaries and keep explanatory copy focused on fundamentals.
Scope: Database-wide _short_description refresh for existing D2 Finance rows;
    _description only for GlobalMacro on chain 4663 at the frozen address below;
    _notes only for the Ethereum HYPE++ Ethena article correction.
Branch: notes/d2-globalmacro-and-note-style
PR title: notes: describe GlobalMacro and simplify D2 vault notes
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1732
Preserves: Custom notes, other notes, flags, fees, tags, links, other long descriptions, non-D2 rows,
    leads, prices, reader state and timestamp caches. No rows are created.
Expected: One summary per existing D2 row, plus at most one GlobalMacro long
    description and one recognised HYPE++ Ethena note. Missing reviewed deployments are reported. Idempotent refresh.

LLM-generated operational migration; changes require human review.
No network reads or price backfill: this changes display copy only.

Run after merge. PR 1731 must land with migration 032 before applying 033-035;
the runner rejects an unseen lower counter after a higher one has applied.
Include all pending migrations in the preview. Inspect the Compose
mounts and build the merged, stamped image with scripts/build-container.sh first.
From ~/vault-scanner/web3-ethereum-defi, quiesce all other metadata writers::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner

Review preview counts before applying. Both modes acquire the lifetime guard;
apply then takes the writer lock. Use the same image and persistent mounts.
Check migration.json against the restarted image's GIT_VERSION_HASH.txt and
verify descriptions after the normal scanner export/upload cycle. The runner
neither scans prices nor publishes exports. Preserve historical state throughout.
"""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.vault import D2_PROTOCOL_NAME, D2_VAULT_SHORT_DESCRIPTIONS, D2Vault, format_d2_vault_note
from eth_defi.vault.base import VaultSpec, normalise_vault_description
from eth_defi.vault_protocols.migration import MigrationContext

#: Only this deployment receives a refreshed long description.
GLOBAL_MACRO = VaultSpec(4663, "0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8")

#: Correct the Base-specific source previously attached to this Ethereum deployment.
HYPE_ETHENA = VaultSpec(1, "0x2406aacbdf8463176deb285adaa81768415b6c7e")


def migrate(context: MigrationContext) -> None:
    """Stage researched display copy after validating protocol attribution.

    Refresh all existing D2 summaries, including an explicit evidence gap for
    undocumented deployments. Validate reviewed addresses before staging any
    edits; the runner owns backups, preview and persistence.

    :param context: Guarded migration context and staged metadata database.
    :return: ``None`` after reporting and committing or previewing the refresh.
    :raises ValueError: If a reviewed deployment has conflicting protocol attribution.
    """
    database = context.metadata()
    reviewed = {VaultSpec(chain_id, address) for chain_id, address in D2_VAULT_SHORT_DESCRIPTIONS}
    for spec in reviewed:
        row = database.rows.get(spec)
        if row is not None and row.get("Protocol") != D2_PROTOCOL_NAME:
            raise ValueError(f"D2 description target {spec} has unexpected protocol {row.get('Protocol')!r}")

    inspected = changed_rows = updated_fields = 0
    preserved_note_specs = []
    for spec, row in database.rows.items():
        if row.get("Protocol") != D2_PROTOCOL_NAME:
            continue
        inspected += 1
        vault = D2Vault(None, spec, features={ERC4626Feature.d2_like})
        values = {"_short_description": vault.short_description}
        if spec == GLOBAL_MACRO:
            values["_description"] = vault.description
        if spec == HYPE_ETHENA:
            stored = normalise_vault_description(row.get("_notes"))
            legacy = normalise_vault_description(format_d2_vault_note(spec.vault_address))
            current = vault.get_notes()
            if not stored or stored == legacy:
                values["_notes"] = current
            elif stored != normalise_vault_description(current):
                preserved_note_specs.append(spec.as_string_id())
        changed = {key: value for key, value in values.items() if row.get(key) != value}
        row.update(changed)
        changed_rows += bool(changed)
        updated_fields += len(changed)

    context.report(
        inspected_rows=inspected,
        changed_rows=changed_rows,
        updated_fields=updated_fields,
        preserved_note_specs=preserved_note_specs,
        missing_specs=sorted(spec.as_string_id() for spec in reviewed if spec not in database.rows),
    )
    context.commit_metadata()

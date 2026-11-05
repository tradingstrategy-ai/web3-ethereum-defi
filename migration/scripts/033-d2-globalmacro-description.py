# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Populate GlobalMacro's researched long description.

Purpose: Publish the launch design and dated manager-reported follow-up.
Scope: Only _description for Robinhood 4663:0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8.
Branch: notes/d2-globalmacro-and-note-style
PR title: notes: describe GlobalMacro and simplify D2 vault notes
PR: See the pull request for branch notes/d2-globalmacro-and-note-style.
Preserves: Summaries, notes, flags, fees, tags, links, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: At most one existing row and one changed field; missing target reported without creation. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. No network reads or price backfill are needed.

Production reminder: after merge, inspect docker-compose.yml and build the stamped
image with scripts/build-container.sh. From ~/vault-scanner/web3-ethereum-defi,
stop persistent writers and quiesce other one-shot/cron writers before preview or apply::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner

Review the preview before applying all pending migrations, including 033.
Use the same inspected image and persistent mounts throughout maintenance. The
runner takes the lifetime guard before the writer lock. Confirm the restarted
image's GIT_VERSION_HASH.txt matches migration.json; normal scanner exports and
uploads publish the description. See .claude/docs/migration-scripts.md.
"""

from eth_defi.erc_4626.vault_protocol.d2.vault import D2_GLOBAL_MACRO_DESCRIPTION, D2_PROTOCOL_NAME
from eth_defi.vault.base import VaultSpec, normalise_vault_description
from eth_defi.vault_protocols.migration import MigrationContext

#: Exact reviewed deployment, frozen on 2026-11-05.
TARGET = VaultSpec(4663, "0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8")


def migrate(context: MigrationContext) -> None:
    """Refresh only the existing GlobalMacro long description.

    Reuse the adapter's maintained copy so later scans preserve the repair.
    Validate the protocol before staging the field; the runner owns backups,
    locks, preview and persistence.

    :param context: Guarded migration context and shared staged database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If the target has conflicting protocol attribution.
    """
    database = context.metadata()
    row = database.rows.get(TARGET)
    if row is None:
        context.report(inspected_rows=0, missing_specs=[TARGET.as_string_id()], updated_fields=0)
        context.commit_metadata()
        return
    if row.get("Protocol") != D2_PROTOCOL_NAME:
        raise ValueError(f"GlobalMacro description target {TARGET} has unexpected protocol {row.get('Protocol')!r}")
    description = normalise_vault_description(D2_GLOBAL_MACRO_DESCRIPTION)
    changed = row.get("_description") != description
    if changed:
        row["_description"] = description
    context.report(inspected_rows=1, missing_specs=[], updated_fields=int(changed))
    context.commit_metadata()

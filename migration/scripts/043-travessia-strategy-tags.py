"""Refresh strategy tags for the seven reviewed Travessia Tauri deployments.

Purpose: Persist real-world credit and lending classifications for Tauri vaults.
Scope: Only _strategy_tags for the seven chain/address pairs in TARGETS below.
Branch: check-curator-travessia-tauri-vault-3
PR title: Add Travessia Credit curator and Tauri strategy classifications
PR: Not opened.
Preserves: Other metadata, unrelated rows, prices, leads and reader state.
Expected: At most seven existing rows and seven changed fields; report missing rows.
Curator attribution resolves during export and needs no persisted refresh.

LLM-generated operational migration; changes require human review.
Stop the scanner and competing writers before preview and apply, following
.claude/docs/migration-scripts.md. Production execution is separate from authoring.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Fixed reviewed share-token inventory; do not expand an applied migration.
TARGETS = (
    VaultSpec(1, "0x4c18e2bb9942b12b28e780acf2d9ec2dda126df9"),
    VaultSpec(1, "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35"),
    VaultSpec(1, "0xf84a37545e313e8177d80039158ed478c7dc9079"),
    VaultSpec(143, "0x8d24b48cb736addb5b7069565f192a052f42ba61"),
    VaultSpec(8453, "0xb37cc5e891d87c5a3ef750e1bdf59c960575593c"),
    VaultSpec(137, "0x08dd5f2a0b107b8f93c4f524901994353bedfaf7"),
    VaultSpec(43114, "0x9c3da70cd7fedeaef528d060ea99ace133e08fd2"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed classifications using the scanner's shared resolver.

    Validate protocol detection and all proposals before staging any changes.
    The runner owns locks, previews, backups and persistence.

    :param context: Guarded migration attempt and staged metadata database.
    :return: ``None`` after reporting and committing or previewing the changes.
    :raises ValueError: If a target has conflicting detection or cannot resolve.
    """
    database = context.metadata()
    rows = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    for spec, row in rows.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.accountable_like not in detection.features:
            raise ValueError(f"Travessia target is not an Accountable vault: {spec}")
    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=rows))
    if skipped or any(update.new_tags is None for update in updates):
        message = "Could not resolve all reviewed Travessia strategy tags"
        raise ValueError(message)
    for update in updates:
        rows[update.spec]["_strategy_tags"] = set(update.new_tags)
    context.report(
        inspected_rows=len(rows),
        changed_rows=len(updates),
        updated_fields=len(updates),
        legacy_tag_rows=invalid,
        missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in rows],
    )
    context.commit_metadata()

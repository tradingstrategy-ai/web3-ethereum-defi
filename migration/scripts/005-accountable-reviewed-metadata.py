"""Refresh reviewed Accountable copy with chain-aware handwritten metadata.

Purpose: Refresh reviewed Accountable copy with chain-aware handwritten metadata.
Scope: Ten frozen deployments; descriptions, maintained notes and Link only. Manager attribution remains unchanged.
Branch: explain-travessia-tauri-vault-3-missing-descriptions
PR title: notes: fill Accountable vault descriptions and add writing standards
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1687
Preserves: Unrelated row fields, leads, historical prices, reader state and timestamp caches.
Expected: Report changed/missing entries; repeated successful applies are skipped.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input.
"""

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.handwritten_metadata import ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES, get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed deployment inventory frozen on 2026-10-28.
TARGETS = (
    VaultSpec(1, "0x0963c0f17d77e336ff75ed36c27d3a1522847615"),
    VaultSpec(1, "0x4c18e2bb9942b12b28e780acf2d9ec2dda126df9"),
    VaultSpec(1, "0xa73424f1ac94b3ef0d0c9af4f2967c87d4af25d9"),
    VaultSpec(1, "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35"),
    VaultSpec(1, "0xf84a37545e313e8177d80039158ed478c7dc9079"),
    VaultSpec(143, "0x4c0d041889281531ff060290d71091401caa786d"),
    VaultSpec(143, "0x8d24b48cb736addb5b7069565f192a052f42ba61"),
    VaultSpec(143, "0xaabab7598be3c4fe58c593e73c2f5934b73b573e"),
    VaultSpec(42161, "0x9fe29a3fec55414f70454e2dbe6522f48ced3e85"),
    VaultSpec(43114, "0x9c3da70cd7fedeaef528d060ea99ace133e08fd2"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed repair and report its committed or proposed fields.

    The context owns backup, persistence and preview staging for the batch.

    :param context: Guarded migration attempt and shared database.
    :return: ``None`` after validation and commit/preview.
    """
    database = context.metadata()
    present = [spec for spec in TARGETS if spec in database.rows]
    if not present:
        raise ValueError("No reviewed Accountable rows exist")
    pending = {}
    for spec in present:
        row = database.rows[spec]
        if row.get("Protocol") != "Accountable":
            raise ValueError(f"Unexpected Accountable protocol: {spec.as_string_id()}")
        metadata = get_handwritten_vault_metadata(spec.chain_id, spec.vault_address)
        if metadata is None:
            raise ValueError(f"Missing handwritten metadata: {spec.as_string_id()}")
        pending[spec] = {"_description": metadata.description, "_short_description": metadata.short_description, "Link": metadata.link, "_notes": ACCOUNTABLE_HANDWRITTEN_VAULT_NOTES[spec.chain_id, spec.vault_address]}
    for spec, fields in pending.items():
        database.rows[spec].update(fields)
    missing = [spec.as_string_id() for spec in TARGETS if spec not in database.rows]
    context.report(inspected_rows=len(present), missing_count=len(missing), missing=missing)
    context.commit_metadata()

"""Refresh the persisted strategy tags of the 3Jane USD3 and sUSD3 vaults.

Purpose: Classify both 3Jane tranches as lending, real-world lending and the
new inventory financing category after the November 2026 Settle forward-flow
agreement.
Scope: Only _strategy_tags for the USD3 and sUSD3 vaults on Ethereum (1).
Branch: find-vaults-3jane-announcement
PR title: feat: add inventory financing strategy tag and rewrite 3Jane descriptions
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/NUMBER
Preserves: All other metadata, unrelated rows, prices, reader state and timestamp caches.
Expected: At most two existing rows and two changed fields; report absent rows.
No network reads or historical scanning are required.

LLM-generated operational migration; changes require human review.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Reviewed 3Jane tranche vaults on Ethereum: USD3 (senior) and sUSD3 (junior).
TARGETS = (
    VaultSpec(1, "0x056b269eb1f75477a8666ae8c7fe01b64dd55ecc"),
    VaultSpec(1, "0xf689555121e529ff0463e191f9bd9d1e496164a7"),
)


def migrate(context: MigrationContext) -> None:
    """Refresh only the reviewed vaults through the maintained 3Jane resolver.

    Restrict collection to the target rows and validate their protocol evidence
    before staging changes. The runner owns preview, backups and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing or previewing the repair.
    :raises ValueError: If a present target is not classified as 3Jane
        or its strategy tags cannot be resolved.
    """
    database = context.metadata()
    present = {spec: database.rows[spec] for spec in TARGETS if spec in database.rows}
    missing = [spec.as_string_id() for spec in TARGETS if spec not in present]

    for spec, row in present.items():
        detection = row.get("_detection_data")
        if row.get("Protocol") != "3Jane" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.threejane_like not in detection.features:
            raise ValueError(f"3Jane target {spec} is not classified as 3Jane")

    updated_fields = 0
    invalid = 0
    if present:
        updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=present))
        if skipped:
            raise ValueError(f"Could not resolve 3Jane strategy tags for {skipped} of {len(present)} target rows")
        for update in updates:
            database.rows[update.spec]["_strategy_tags"] = None if update.new_tags is None else set(update.new_tags)
        updated_fields = len(updates)

    context.report(inspected_rows=len(present), missing_specs=missing, updated_fields=updated_fields, legacy_tag_rows=invalid)
    context.commit_metadata()

"""Repair historical Fraxlend protocol classifications.

Fraxlend pairs discovered before the generic ``FraxlendPair`` probe was added
were persisted as generic ERC-4626 vaults. Their price history remains valid:
both readers obtain NAV through ``totalAssets()``. This migration updates only
the persisted feature sets and exported protocol label.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/repair-fraxlend-features.py

Environment variables:

- ``VAULT_DB``: Vault metadata pickle path.
- ``DRY_RUN``: Set to ``true`` to report without writing.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)


#: Fraxlend pair contracts verified as ``FraxlendPair`` on Ethereum and Arbitrum.
#: Source: https://github.com/FraxFinance/fraxlend
FRAXLEND_SPECS = frozenset(
    {
        VaultSpec(1, address)
        for address in (
            "0x0601b72bef2b3f09e9f48b7d60a8d7d2d3800c6e",
            "0x1c0c222989a37247d974937782cebc8bf4f25733",
            "0x1fff4a418471a7b44efa023320e02dcdb486ed77",
            "0x254fbc9dbb12c446ea5c9a4439c816d34b875920",
            "0x281e6cb341a552e4faccc6b4eef1a6fcc523682d",
            "0x28cdf6ce79702aaefbf217cf98cbd11f5639b9f1",
            "0x35e08b28d5b01d058cbb1c39da9188cc521a79af",
            "0x3a25b9ab8c07ffefee614531c75905e810d8a239",
            "0x470c677af6cce089ac38245332bfa03b22b4caed",
            "0x48f32b7c960fd0280297f6f0182e2607a3398db5",
            "0x7093f6141293f7c4f67e5efd922ac934402e452d",
            "0x76ff120ff669591b7cb5452995c0269437bea414",
            "0x78bb3aec3d855431bd9289fd98da13f9ebb7ef15",
            "0x8087346b8865e5b0bf9f8a49742c2d83f6a50a6c",
            "0x82ec28636b77661a95f021090f6be0c8d379dd5d",
            "0x8c9db7a9329f221ed1ffe56bf4bd073aa320eed9",
            "0x8e5f09de0cd7841239410f929a905e214443d9e0",
            "0xa4ddd4770588ef97a3a03e4b7e3885d824159baa",
            "0xab3cb84c310186b2fa4b4503624a5d90b5dcb22d",
            "0xb49b5cf2efb64d97825c70706a49059e93625865",
            "0xb5a46f712f03808ae5c4b885c6f598fa06442684",
            "0xb5ae5b75c0df5632c572a657109375646ce66f90",
            "0xbe08194b3f4ae9cd80bd7f553a9a782c0ed65d17",
            "0xc045a53936d793839bfca146058976ef4285161e",
            "0xc6cada314389430d396c7b0c70c6281e99ca7fe8",
            "0xc779fee076eb04b9f8ea424ec19de27efd17a68d",
            "0xd1887398f3bbdc9d10d0d5616ad83506ddf5057a",
            "0xeca60a11c49486088ad7c5e4ad7dae2c061dbb1c",
            "0xee847a804b67f4887c9e8fe559a2da4278defb52",
        )
    }
    | {
        VaultSpec(42161, address)
        for address in (
            "0x2d0483fefaba4325c7521539a3dfacf94a19c472",
            "0x6076ebdfe17555ed3e6869cf9c373bbd9ad55d38",
            "0x9168ac3a83a31bd85c93f4429a84c05db2caef08",
            "0xc37aa0cf7e45fe0e811d99062020080147970a1a",
        )
    }
)


@dataclass(slots=True, frozen=True)
class FraxlendFeatureRepairResult:
    """Outcome of a Fraxlend metadata repair.

    :param matched_rows: Number of recognised Fraxlend rows in the database.
    :param repaired_rows: Number of metadata rows changed.
    """

    matched_rows: int
    repaired_rows: int


def repair_fraxlend_features(vault_db_path: Path = DEFAULT_VAULT_DATABASE, *, dry_run: bool) -> FraxlendFeatureRepairResult:
    """Mark known historical Fraxlend pairs with the Frax feature.

    Both the top-level feature field and the stored discovery object are
    updated so exports and subsequent price scans use the Frax adapter.

    :param vault_db_path: Vault metadata database to repair.
    :param dry_run: Report changes without writing the database.
    :return: Matching and repair counters.
    """

    db = VaultDatabase.read(vault_db_path)
    matched_rows = 0
    repaired_rows = 0
    protocol_name = get_vault_protocol_name({ERC4626Feature.frax_like})

    for spec, row in db.rows.items():
        if spec not in FRAXLEND_SPECS:
            continue

        matched_rows += 1
        changed = False
        features = set(row.get("features") or set())
        if ERC4626Feature.frax_like not in features:
            features.add(ERC4626Feature.frax_like)
            row["features"] = features
            changed = True

        detection = row.get("_detection_data")
        if isinstance(detection, ERC4262VaultDetection) and ERC4626Feature.frax_like not in detection.features:
            detection.features.add(ERC4626Feature.frax_like)
            changed = True

        if row.get("Protocol") != protocol_name:
            row["Protocol"] = protocol_name
            changed = True

        if changed:
            repaired_rows += 1
            logger.info("Repairing Fraxlend metadata for %s", spec)

    result = FraxlendFeatureRepairResult(matched_rows=matched_rows, repaired_rows=repaired_rows)
    if result.repaired_rows == 0:
        logger.info("No Fraxlend metadata rows need repair in %s", vault_db_path)
        return result

    if dry_run:
        logger.info("DRY RUN: would repair %d Fraxlend rows in %s", result.repaired_rows, vault_db_path)
        return result

    backup_path = vault_db_path.with_suffix(".pickle.bak-fraxlend-repair")
    logger.info("Creating vault DB backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    db.write(vault_db_path)
    logger.info("Repaired %d Fraxlend metadata rows in %s", result.repaired_rows, vault_db_path)
    return result


def main() -> None:
    """Run the Fraxlend metadata repair command."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB", DEFAULT_VAULT_DATABASE)).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    assert vault_db_path.exists(), f"Vault database not found: {vault_db_path}"

    result = repair_fraxlend_features(vault_db_path, dry_run=dry_run)
    print(f"Matched {result.matched_rows:,} Fraxlend rows, repaired {result.repaired_rows:,}")
    if dry_run:
        print("Dry run - no changes written.")


if __name__ == "__main__":
    main()

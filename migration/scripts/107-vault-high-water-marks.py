# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Refresh reviewed high-water-mark and fee classifications.

Purpose: Export performance-fee scope and correct the Pear and Lagoon fee modes.
Scope: Four reviewed Pear deployments on HyperEVM and saved Lagoon vaults with Lagoon detection.
Branch: investigate-pear-net-fees
PR title: feat: export Pear and Lagoon fee accounting and high-water marks
PR: See the pull request for branch investigate-pear-net-fees.
Preserves: Fee rates, unrelated metadata, price history, leads and reader state.
Expected: Refresh up to two fields per qualifying vault.

LLM-generated operational migration. Changes require human review.
Run through the numbered migration runner with the scanner stopped.
"""

from copy import copy

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID
from eth_defi.erc_4626.vault_protocol.pear.vault import PearVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, get_vault_fee_mode
from eth_defi.vault.vaultdb import VaultRow
from eth_defi.vault_protocols.migration import MigrationContext

#: Deployments whose fee accounting was checked on HyperEVM.
PEAR_TARGETS = (
    VaultSpec(PEAR_CHAIN_ID, "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096"),
    VaultSpec(PEAR_CHAIN_ID, "0xd38a67a4f54a9b4286d72f08440485a395ddfa70"),
    VaultSpec(PEAR_CHAIN_ID, "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84"),
    VaultSpec(PEAR_CHAIN_ID, "0x69f2ed780a4cec49e552dbcd35a60f41368bc362"),
)


def migrate(context: MigrationContext) -> None:
    """Stage the reviewed fee-accounting classifications without RPC calls.

    Stage changes on copies so a conflicting identity leaves the catalogue
    intact. Reuse the adapter defaults and preserve the scanned fee rates.

    :param context: Guarded migration attempt and staged metadata owner.
    :return: None after preview or commit.
    :raises ValueError: If a present Pear target has conflicting metadata.
    """
    database = context.metadata()
    staged: dict[VaultSpec, VaultRow] = {}
    skipped_lagoon_specs = []
    for spec, row in database.rows.items():
        protocol = row.get("Protocol")
        if spec not in PEAR_TARGETS and protocol not in ("Lagoon Finance", "Lagoon"):
            continue
        detection = row.get("_detection_data")
        valid_identity = isinstance(detection, ERC4262VaultDetection) and detection.get_spec() == spec
        if spec in PEAR_TARGETS:
            if protocol != "Pear" or not valid_identity or ERC4626Feature.pear_like not in detection.features:
                raise ValueError(f"Conflicting Pear vault identity for {spec.as_string_id()}")
            high_water_mark = PearVault.high_water_mark
        elif valid_identity and ERC4626Feature.lagoon_like in detection.features:
            high_water_mark = LagoonVault.high_water_mark
        else:
            skipped_lagoon_specs.append(spec.as_string_id())
            continue

        updated = row | {"_high_water_mark": high_water_mark}
        fees = row.get("_fees")
        mode = get_vault_fee_mode(protocol, spec.vault_address)
        if isinstance(fees, FeeData) and fees.fee_mode != mode:
            # Old pickles may contain Decimal rates. A shallow copy preserves
            # them without invoking FeeData's constructor validation.
            fees = copy(fees)
            fees.fee_mode = mode
            updated["_fees"] = fees
        staged[spec] = updated

    database.rows.update(staged)
    context.report(classified_rows=len(staged), skipped_lagoon_specs=skipped_lagoon_specs, missing_pear_specs=[spec.as_string_id() for spec in PEAR_TARGETS if spec not in database.rows])
    context.commit_metadata()

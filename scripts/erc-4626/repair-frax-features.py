"""Repair historical Frax vault protocol classifications.

Fraxlend pairs and stablecoin staking vaults discovered before their Frax
routing was added were persisted as generic ERC-4626 vaults. Their price
history remains valid because all readers obtain NAV through ``totalAssets()``.
This migration updates the persisted feature sets, protocol label, fee data,
links and protocol-owned descriptive metadata. It does not alter vault leads,
reader state or any price-history Parquet file.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/repair-frax-features.py

Environment variables:

- ``VAULT_DB``: Vault metadata pickle path.
- ``DRY_RUN``: Set to ``true`` to report without writing.
- ``JSON_RPC_ETHEREUM`` and ``JSON_RPC_ARBITRUM``: Archive-capable RPC URLs
  for chains containing matched Fraxlend pairs.
"""

import datetime
import logging
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.frax.constants import (
    FRAX_STAKING_VAULT_METADATA_BY_CHAIN,
    FRAX_STAKING_VAULTS_BY_CHAIN,
    FRAXLEND_NOTES,
    FRAXLEND_SHORT_DESCRIPTION,
)
from eth_defi.erc_4626.vault_protocol.frax.vault import fetch_fraxlend_protocol_fee
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.types import Percent
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import get_notes
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


#: Fraxlend pair contracts recovered from both historical signatures of
#: ``FraxlendPairDeployer.LogDeploy`` on Ethereum and Arbitrum, then confirmed
#: through ``DEPLOYER_ADDRESS()``.
#:
#: Source: https://github.com/FraxFinance/fraxlend/blob/main/src/contracts/FraxlendPairDeployer.sol
FRAXLEND_SPECS = frozenset(
    {
        VaultSpec(1, address)
        for address in (
            "0x0601b72bef2b3f09e9f48b7d60a8d7d2d3800c6e",
            "0x00c242ca3ef5c2cb909ed3ed972b6f24624b4337",
            "0x138e6b890d14a07c11f69b51dca9270968b01b8b",
            "0x1c0c222989a37247d974937782cebc8bf4f25733",
            "0x1d95c12d7a8d525f8d8cb0c44814b12cd13dfa01",
            "0x1ee17be9b788bcf3eb86c08d0e2b049e036f6eb1",
            "0x1fff4a418471a7b44efa023320e02dcdb486ed77",
            "0x254fbc9dbb12c446ea5c9a4439c816d34b875920",
            "0x281e6cb341a552e4faccc6b4eef1a6fcc523682d",
            "0x28cdf6ce79702aaefbf217cf98cbd11f5639b9f1",
            "0x35e08b28d5b01d058cbb1c39da9188cc521a79af",
            "0x37110563e3856d413b821f07c7e3991c4493673d",
            "0x3a25b9ab8c07ffefee614531c75905e810d8a239",
            "0x427d3b3b645ec5cbaa321cdcab1af1d9ce4c0805",
            "0x470c677af6cce089ac38245332bfa03b22b4caed",
            "0x48f32b7c960fd0280297f6f0182e2607a3398db5",
            "0x501256b1dc7bb99cde57af188a69307bcc7e00e8",
            "0x54e20b542eed95e6c7d8f29ad46a3cf5661c3048",
            "0x5ea719aa26c158b7b0f5b6363c6bebcf4ae3cdb6",
            "0x5ee658b7f46fd9dee4608ae9c8e7d78aad7509c2",
            "0x5f9deea62fdd79efdd22fd714670e53de33463a5",
            "0x672500520a4734af8401915dbdda42f6e01bfba9",
            "0x6a80be9df5f46eb11d0c6ed706607f72c0d1c337",
            "0x7093f6141293f7c4f67e5efd922ac934402e452d",
            "0x7640aeed096982adad9c8ba668ebe35c1f6551de",
            "0x76ff120ff669591b7cb5452995c0269437bea414",
            "0x78bb3aec3d855431bd9289fd98da13f9ebb7ef15",
            "0x8087346b8865e5b0bf9f8a49742c2d83f6a50a6c",
            "0x809d2fbffa273334b73a5d1f070ee421a0a4f3c1",
            "0x81268a140ae319c744388d1e4203b21bf8879129",
            "0x82ec28636b77661a95f021090f6be0c8d379dd5d",
            "0x8c9db7a9329f221ed1ffe56bf4bd073aa320eed9",
            "0x8e5f09de0cd7841239410f929a905e214443d9e0",
            "0x9234ea70c2360576c3c9e706e7f3f3219e6d885c",
            "0xa4ddd4770588ef97a3a03e4b7e3885d824159baa",
            "0xa96eb85d1afb297bb67eb4856ad5a469ec921d00",
            "0xa042f783b33f78e7580608d1698fa35b2dec245a",
            "0xa201e7bbb7ca60ab88514d12dd91fc6748e6d050",
            "0xab3cb84c310186b2fa4b4503624a5d90b5dcb22d",
            "0xb49b5cf2efb64d97825c70706a49059e93625865",
            "0xb5a46f712f03808ae5c4b885c6f598fa06442684",
            "0xb5ae5b75c0df5632c572a657109375646ce66f90",
            "0xb5fbab8ebd088c512add09a0cf3ffa9f9ca437bf",
            "0xb67bd04f74bd79a505c5167675e8812355270ed5",
            "0xbb95286b7973f8b217ce903ca15c75528cbd8e69",
            "0xbe08194b3f4ae9cd80bd7f553a9a782c0ed65d17",
            "0xc045a53936d793839bfca146058976ef4285161e",
            "0xc16b81e004288c465eaadf080028994044a3c69f",
            "0xc653f61ba422f97beb141b34580906184f3765a2",
            "0xc6cada314389430d396c7b0c70c6281e99ca7fe8",
            "0xc779fee076eb04b9f8ea424ec19de27efd17a68d",
            "0xce2c6d5dd135d1190822ce0e21edd4adf41350c9",
            "0xcfe3550206ea801c3f7dc7997376f09e0b7e4b81",
            "0xd1887398f3bbdc9d10d0d5616ad83506ddf5057a",
            "0xdb856afae2a71eb20bd47d5dc37def1d83d29fd7",
            "0xe1b6a8c4a044b38fcd862ba509844c54393cc737",
            "0xeca60a11c49486088ad7c5e4ad7dae2c061dbb1c",
            "0xedba32045bb3d69e42e6bcb6ef6f1337c89c76c8",
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

#: Historical Frax vault rows and their concrete routing feature.
FRAX_FEATURE_BY_SPEC = {
    **dict.fromkeys(FRAXLEND_SPECS, ERC4626Feature.frax_like),
    **{VaultSpec(chain_id, address): ERC4626Feature.frax_staking_like for chain_id, addresses in FRAX_STAKING_VAULTS_BY_CHAIN.items() for address in addresses},
}


@dataclass(slots=True, frozen=True)
class FraxFeatureRepairResult:
    """Outcome of a Frax metadata repair.

    :param matched_rows: Number of recognised Frax rows in the database.
    :param repaired_rows: Number of metadata rows changed.
    """

    matched_rows: int
    repaired_rows: int


@dataclass(slots=True, frozen=True)
class FraxCachedMetadata:
    """Metadata persisted by a repaired Frax vault scan.

    This structure combines protocol-owned copy with the separately fetched
    per-pair fee snapshot used by the Fraxlend reader.

    :param fees:
        Structured vault fees used by return calculations.
    :param link:
        Native Frax product page.
    :param short_description:
        One-line vault listing description.
    :param notes:
        Longer Markdown vault note.
    """

    #: Structured vault fees used by return calculations.
    fees: FeeData

    #: Native Frax product page.
    link: str

    #: One-line vault listing description.
    short_description: str

    #: Longer Markdown vault note.
    notes: str


def create_frax_cached_metadata(
    spec: VaultSpec,
    feature: ERC4626Feature,
    protocol_name: str,
    *,
    fraxlend_performance_fee: Percent | None = None,
) -> FraxCachedMetadata:
    """Create repaired scan metadata for one reviewed Frax vault.

    Fraxlend uses family-level lending copy and an internalised protocol fee.
    Staking products use address-specific hardcoded copy and are feeless. Any
    shared manual safety note retains priority over the protocol-owned note.

    :param spec:
        Reviewed Frax vault identifier.
    :param feature:
        Concrete Frax product-family feature.
    :param protocol_name:
        Display protocol name used for shared manual-note lookup.
    :param fraxlend_performance_fee:
        Per-pair fee read from ``currentRateInfo()``. Required for Fraxlend.
    :return:
        Cached values equivalent to a fresh protocol reader scan.
    """

    manual_notes = get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=protocol_name)
    if feature == ERC4626Feature.frax_like:
        if fraxlend_performance_fee is None:
            raise ValueError(f"Onchain Fraxlend fee is required for {spec}")
        return FraxCachedMetadata(
            fees=FeeData(
                fee_mode=VaultFeeMode.internalised_minting,
                management=0.0,
                performance=fraxlend_performance_fee,
                deposit=0.0,
                withdraw=0.0,
            ),
            link=f"https://app.frax.finance/fraxlend/pair/{spec.vault_address}",
            short_description=FRAXLEND_SHORT_DESCRIPTION,
            notes=manual_notes or FRAXLEND_NOTES,
        )

    staking_metadata = FRAX_STAKING_VAULT_METADATA_BY_CHAIN[spec.chain_id][spec.vault_address]
    return FraxCachedMetadata(
        fees=FeeData(
            fee_mode=VaultFeeMode.feeless,
            management=0.0,
            performance=0.0,
            deposit=0.0,
            withdraw=0.0,
        ),
        link="https://frax.com/earn",
        short_description=staking_metadata.short_description,
        notes=manual_notes or staking_metadata.notes,
    )


def fetch_fraxlend_fees(specs: Iterable[VaultSpec]) -> dict[VaultSpec, Percent]:
    """Fetch a consistent on-chain fee snapshot for Fraxlend repair rows.

    Each chain is pinned to its current head before any pair reads begin, so
    all fees from that chain describe the same block. RPC URLs use the standard
    ``JSON_RPC_<CHAIN>`` environment-variable convention.

    :param specs:
        Fraxlend pairs whose cached metadata will be repaired.
    :return:
        Per-pair protocol fee fractions keyed by vault specification.
    """

    specs_by_chain: dict[int, list[VaultSpec]] = {}
    for spec in specs:
        specs_by_chain.setdefault(spec.chain_id, []).append(spec)

    fees: dict[VaultSpec, Percent] = {}
    for chain_id, chain_specs in sorted(specs_by_chain.items()):
        web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
        block_number = web3.eth.block_number
        logger.info("Reading %d Fraxlend fees on chain %d at block %d", len(chain_specs), chain_id, block_number)
        fees.update({spec: fetch_fraxlend_protocol_fee(web3, spec.vault_address, block_number) for spec in chain_specs})

    return fees


def validate_fraxlend_fee_coverage(
    vault_rows: Mapping[VaultSpec, VaultRow],
    fraxlend_fees: Mapping[VaultSpec, Percent] | None,
) -> None:
    """Reject a repair that lacks on-chain fees for matched Fraxlend rows.

    :param vault_rows:
        Persisted vault metadata keyed by vault specification.
    :param fraxlend_fees:
        On-chain fee snapshot supplied to the repair.
    :raise ValueError:
        If any matched Fraxlend row lacks an on-chain value.
    """

    missing_fee_specs = set(vault_rows).intersection(FRAXLEND_SPECS).difference(fraxlend_fees or {})
    if missing_fee_specs:
        missing_list = ", ".join(sorted(spec.as_string_id() for spec in missing_fee_specs))
        raise ValueError(f"Missing onchain fees for Fraxlend repair rows: {missing_list}")


def update_row_value(row: VaultRow, key: str, value: object) -> bool:
    """Set a cached vault row value when it differs.

    :param row:
        Mutable vault metadata row.
    :param key:
        Persisted row field name.
    :param value:
        Expected Frax metadata value.
    :return:
        ``True`` when the row changed.
    """

    if row.get(key) == value:
        return False
    row[key] = value
    return True


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting backup path for the vault database.

    :param vault_db_path:
        Existing metadata pickle to protect before repair.
    :return:
        A sibling backup path that does not already exist.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-frax-repair")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def repair_frax_features(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
    fraxlend_fees: Mapping[VaultSpec, Percent] | None = None,
) -> FraxFeatureRepairResult:
    """Mark known historical Fraxlend and staking vaults as Frax.

    The protocol identity and all static metadata supplied by the Frax readers
    are updated together. This prevents exports from combining the new Frax
    label with stale generic-reader fees, links, descriptions or notes.

    :param vault_db_path: Vault metadata database to repair.
    :param dry_run: Report changes without writing the database.
    :param fraxlend_fees:
        Per-pair on-chain fee snapshot. Required for every matched Fraxlend row.
    :return: Matching and repair counters.
    """

    db = VaultDatabase.read(vault_db_path)
    matched_rows = 0
    repaired_rows = 0
    protocol_name = get_vault_protocol_name({ERC4626Feature.frax_like})
    validate_fraxlend_fee_coverage(db.rows, fraxlend_fees)

    for spec, row in db.rows.items():
        feature = FRAX_FEATURE_BY_SPEC.get(spec)
        if feature is None:
            continue

        matched_rows += 1
        expected_features = {feature}
        changed = update_row_value(row, "features", expected_features)

        detection = row.get("_detection_data")
        if isinstance(detection, ERC4262VaultDetection) and detection.features != expected_features:
            detection.features.clear()
            detection.features.update(expected_features)
            changed = True

        fraxlend_performance_fee = fraxlend_fees[spec] if feature == ERC4626Feature.frax_like and fraxlend_fees is not None else None
        cached_metadata = create_frax_cached_metadata(
            spec,
            feature,
            protocol_name,
            fraxlend_performance_fee=fraxlend_performance_fee,
        )
        fee_data = cached_metadata.fees
        updates = {
            "Protocol": protocol_name,
            "Features": ", ".join(sorted(item.name for item in expected_features)),
            "Mgmt fee": fee_data.management,
            "Perf fee": fee_data.performance,
            "Deposit fee": fee_data.deposit,
            "Withdraw fee": fee_data.withdraw,
            "_fees": fee_data,
            "Link": cached_metadata.link,
            "_lockup": datetime.timedelta(0),
            "_short_description": cached_metadata.short_description,
            "_notes": cached_metadata.notes,
        }
        metadata_changes = [update_row_value(row, key, value) for key, value in updates.items()]
        changed = any(metadata_changes) or changed

        if changed:
            repaired_rows += 1
            logger.info("Repairing Frax metadata for %s as %s", spec, feature.value)

    result = FraxFeatureRepairResult(matched_rows=matched_rows, repaired_rows=repaired_rows)
    if result.repaired_rows == 0:
        logger.info("No Frax metadata rows need repair in %s", vault_db_path)
        return result

    if dry_run:
        logger.info("DRY RUN: would repair %d Frax rows in %s", result.repaired_rows, vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault DB backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    db.write(vault_db_path)
    logger.info("Repaired %d Frax metadata rows in %s", result.repaired_rows, vault_db_path)
    return result


def main() -> None:
    """Run the Frax metadata repair command."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB", DEFAULT_VAULT_DATABASE)).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    assert vault_db_path.exists(), f"Vault database not found: {vault_db_path}"

    vault_db = VaultDatabase.read(vault_db_path)
    fraxlend_specs = set(vault_db.rows).intersection(FRAXLEND_SPECS)
    fraxlend_fees = fetch_fraxlend_fees(fraxlend_specs)
    result = repair_frax_features(vault_db_path, dry_run=dry_run, fraxlend_fees=fraxlend_fees)
    print(f"Matched {result.matched_rows:,} Frax rows, repaired {result.repaired_rows:,}")
    if dry_run:
        print("Dry run - no changes written.")


if __name__ == "__main__":
    main()

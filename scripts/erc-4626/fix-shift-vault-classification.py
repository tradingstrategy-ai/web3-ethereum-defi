#!/usr/bin/env python3
"""Repair the three reviewed Shift vaults' current classification metadata.

Shift's custom DeFi vaults were incorrectly treated as legally structured
tokenised funds. This repair reads the actual account-admission policy and
removes only that erroneous flag. Oracle validation and executor settlement
are independent of investor admission; see
https://github.com/SHIFT-NebulaLabs/shift-core-v1/blob/main/src/ShiftVault.sol.

Run with the scanner environment and its existing mounted state::

    DRY_RUN=true poetry run python scripts/erc-4626/fix-shift-vault-classification.py
    DRY_RUN=false poetry run python scripts/erc-4626/fix-shift-vault-classification.py

``DRY_RUN`` defaults to true. ``VAULT_DB_PATH`` overrides the metadata pickle
under ``PIPELINE_DATA_DIR``. ``JSON_RPC_BASE`` and ``JSON_RPC_ARBITRUM`` supply
current-state RPC connections. ``PIPELINE_LOCK_TIMEOUT`` defaults to 60 seconds.
``LOG_LEVEL`` defaults to info. Persistent mode holds the scanner writer lock,
backs up the original file and atomically replaces it after all reads succeed.
Prices, reader state and timestamp caches are never opened or modified.
"""

import logging
import os
import shutil
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.shift.constants import SHIFT_VAULT_PRODUCTS
from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Fixed migration scope reviewed against live contracts on 2026-10-27.
REVIEWED_SHIFT_VAULTS = frozenset(
    {
        (8453, "0xaf69bf9ea9e0166498c0502af5b5945980ed1e0e"),
        (8453, "0x4ce3ec1b7b4ffb33a0b70c64a0560a3f341aa2e1"),
        (42161, "0x956bdd9c18b786b082fd50c52722d254f0cb6964"),
    }
)

VaultFactory = Callable[[Web3, VaultSpec], ShiftVault]


@dataclass(slots=True, frozen=True)
class ShiftMetadataUpdate:
    """One validated correction to a reviewed Shift metadata row."""

    #: Exact chain and address of the corrected row.
    spec: VaultSpec

    #: Old values of changed metadata fields for the operator's report.
    old_values: dict[str, object]

    #: Replacement values of changed metadata fields only.
    new_values: dict[str, object]


def parse_dry_run() -> bool:
    """Read the migration's strict opt-in write setting.

    Invalid settings fail before loading or modifying the database.

    :return:
        Whether the migration is read-only; defaults to true.
    :raises ValueError:
        If ``DRY_RUN`` is not a recognised boolean literal.
    """

    value = os.environ.get("DRY_RUN", "true").lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    raise ValueError(f"DRY_RUN must be true or false, got {value!r}")


def fetch_updates(
    vault_db: VaultDatabase,
    web3_by_chain: Mapping[int, Web3],
    vault_factory: VaultFactory = ShiftVault,
) -> tuple[ShiftMetadataUpdate, ...]:
    """Validate the complete fixed scope and read current policy corrections.

    All validation and network reads finish before any row is changed. RPC
    failures propagate, so the repair cannot persist a partial or unknown policy.

    :param vault_db:
        Existing trusted scanner database.
    :param web3_by_chain:
        Verified current-state clients keyed by chain id.
    :param vault_factory:
        Adapter constructor, injectable for offline migration tests.
    :return:
        Changed metadata fields for the reviewed rows only.
    :raises ValueError:
        If any reviewed row, chain or detection envelope is invalid.
    """

    specs = tuple(VaultSpec(chain_id, address) for chain_id, address in sorted(REVIEWED_SHIFT_VAULTS))
    for spec in specs:
        if (spec.chain_id, spec.vault_address) not in SHIFT_VAULT_PRODUCTS:
            raise ValueError(f"Reviewed Shift vault is missing from the registry: {spec}")
        if spec not in vault_db.rows:
            raise ValueError(f"Missing reviewed Shift metadata row: {spec}")
        web3 = web3_by_chain.get(spec.chain_id)
        if web3 is None or web3.eth.chain_id != spec.chain_id:
            raise ValueError(f"Missing or incorrect chain connection for {spec}")
        row = vault_db.rows[spec]
        detection = row.get("_detection_data")
        if row.get("Protocol") != "Shift":
            raise ValueError(f"Unexpected protocol for reviewed Shift row: {spec}")
        if not isinstance(detection, ERC4262VaultDetection) or detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address:
            raise ValueError(f"Invalid detection envelope for reviewed Shift row: {spec}")
        if ERC4626Feature.shift_like not in detection.features:
            raise ValueError(f"Missing Shift feature for reviewed row: {spec}")

    updates: list[ShiftMetadataUpdate] = []
    for spec in tqdm(specs, desc="Reading Shift admission and closure metadata"):
        row = vault_db.rows[spec]
        vault = vault_factory(web3_by_chain[spec.chain_id], spec)
        whitelisted = vault.is_whitelisted_deposit()
        assert type(whitelisted) is bool, f"Invalid Shift policy result for {spec}: {whitelisted!r}"
        old_flags = set(row.get("_flags") or ())
        replacements = {
            "_flags": old_flags - {VaultFlag.tokenised_fund, VaultFlag.tokenised_fund.value},
            "_deposit_permission": VaultDepositPermission.whitelisted.value if whitelisted else VaultDepositPermission.permissionless.value,
            "_whitelist_notes": vault.get_whitelist_notes(),
            "_deposit_manager": vault.get_deposit_manager_capability().as_initial_public_schema(),
            "_deposit_closed_reason": vault.fetch_deposit_closed_reason(),
            "_redemption_closed_reason": vault.fetch_redemption_closed_reason(),
            # No authoritative global availability read is implemented.
            "_deposits_open": vault.fetch_deposit_open(),
        }
        changed = {key: value for key, value in replacements.items() if row.get(key) != value}
        if changed:
            updates.append(ShiftMetadataUpdate(spec, {key: row.get(key) for key in changed}, changed))
    return tuple(updates)


def repair_shift_metadata(
    vault_db_path: Path,
    *,
    dry_run: bool = True,
    web3_by_chain: Mapping[int, Web3] | None = None,
    vault_factory: VaultFactory = ShiftVault,
    lock_timeout: float = 60,
) -> tuple[ShiftMetadataUpdate, ...]:
    """Repair current Shift metadata with a backup and atomic replacement.

    Persistent calls hold the shared scanner writer lock from load to write.
    Unchanged and dry-run calls create neither backups nor replacement files.

    :param vault_db_path:
        Existing metadata pickle; no missing database is created.
    :param dry_run:
        Report proposed updates without writing, defaulting to true.
    :param web3_by_chain:
        Optional current-state clients for testing or operator reuse.
    :param vault_factory:
        Adapter constructor, injectable for offline tests.
    :param lock_timeout:
        Seconds to wait for the shared pipeline writer lock.
    :return:
        Exact changed fields, whether proposed or persisted.
    """

    vault_db_path = vault_db_path.expanduser().resolve()
    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent / "scan-pipeline", timeout=lock_timeout)
    with lock:
        vault_db = VaultDatabase.read(vault_db_path)
        if web3_by_chain is None:
            web3_by_chain = {chain_id: create_multi_provider_web3(read_json_rpc_url(chain_id), expected_chain_id=chain_id) for chain_id in sorted({chain_id for chain_id, _ in REVIEWED_SHIFT_VAULTS})}
        updates = fetch_updates(vault_db, web3_by_chain, vault_factory)
        if dry_run or not updates:
            return updates

        timestamp = native_datetime_utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = vault_db_path.with_name(f"{vault_db_path.name}.bak-shift-{timestamp}")
        logger.info("Backing up Shift metadata to %s", backup_path)
        with backup_path.open("xb") as backup, vault_db_path.open("rb") as original:
            shutil.copyfileobj(original, backup)
        for update in updates:
            vault_db.rows[update.spec] = {**vault_db.rows[update.spec], **update.new_values}
        vault_db.write(vault_db_path)
        return updates


def main() -> None:
    """Report or apply the fixed-scope Shift metadata correction.

    Settings come from environment variables documented in the module header.
    The table lists every changed field and its previous and replacement value.

    :return:
        None after reporting the migration outcome.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_dry_run()
    path = Path(os.environ.get("VAULT_DB_PATH", str(get_pipeline_data_dir() / "vault-metadata-db.pickle"))).expanduser()
    updates = repair_shift_metadata(path, dry_run=dry_run, lock_timeout=float(os.environ.get("PIPELINE_LOCK_TIMEOUT", "60")))
    report = [(update.spec.chain_id, update.spec.vault_address, key, update.old_values[key], value) for update in updates for key, value in update.new_values.items()]
    if report:
        print(tabulate(report, headers=("Chain", "Address", "Field", "Before", "After"), tablefmt="rounded_outline"))
    logger.info("Shift metadata repair: inspected=%d, changed=%d, dry_run=%s", len(REVIEWED_SHIFT_VAULTS), len(updates), dry_run)


if __name__ == "__main__":
    main()

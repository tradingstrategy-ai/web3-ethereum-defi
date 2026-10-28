#!/usr/bin/env python3
"""Refresh cached Flying Tulip staking metadata without a chain reset.

The historical scanner may already contain reviewed staking vault rows from
generic ERC-4626 discovery. This targeted migration rebuilds the selected rows
with the correct Flying Tulip adapter, preserving unrelated records, reader-state and
Parquet history. It never deletes a chain-wide cache or recreates discovery
history.

Usage::

    source .local-test.env && DRY_RUN=true poetry run python scripts/erc-4626/migrate-flying-tulip-vault-metadata.py
    source .local-test.env && DRY_RUN=false poetry run python scripts/erc-4626/migrate-flying-tulip-vault-metadata.py

Environment variables:

- ``VAULT_DB_PATH``: Optional metadata pickle path.
- ``DRY_RUN``: Report changes without writing; defaults to ``true``.
- ``CHAIN_ID`` and ``VAULT_ADDRESS``: Optional paired filters. An explicit
  target must be reviewed and already present. Without filters only the
  original three sftUSD deployments are refreshed.
- ``JSON_RPC_ETHEREUM``, ``JSON_RPC_BINANCE`` and ``JSON_RPC_SONIC``: Required
  only for reviewed vault rows present in the metadata cache.
"""

import dataclasses
import logging
import os
import shutil
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import FLYING_TULIP_PROTOCOLS_BY_CHAIN
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_SFTUSD_BY_CHAIN
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.scanner_lock import acquire_scanner_lifecycle_guard
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class FlyingTulipMetadataMigrationResult:
    """Summarise the targeted Flying Tulip metadata migration."""

    #: Reviewed addresses found in the existing metadata cache.
    inspected_rows: int
    #: Rows rebuilt with the Flying Tulip adapter.
    migrated_rows: int


def _parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse one strict boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value used when the variable is absent.
    :return:
        Parsed boolean value.
    :raises ValueError:
        If the supplied value is not a recognised boolean literal.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes"}:
        return True
    if value.lower() in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def _create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting backup path beside the metadata pickle.

    :param vault_db_path:
        Existing metadata cache to protect.
    :return:
        Available sibling backup path.
    """

    candidate = Path(f"{vault_db_path}.bak-flying-tulip-metadata")
    index = 1
    while candidate.exists():
        candidate = Path(f"{vault_db_path}.bak-flying-tulip-metadata.{index}")
        index += 1
    return candidate


def migrate_flying_tulip_metadata(vault_db: VaultDatabase, *, dry_run: bool, target: VaultSpec | None = None) -> FlyingTulipMetadataMigrationResult:
    """Refresh existing reviewed staking rows in a metadata database.

    Each replacement preserves the original discovery counts and first-seen
    fields. Rows not yet discovered are left untouched for ordinary hardcoded
    lead discovery; this migration never manufactures activity evidence.

    :param vault_db:
        Existing persisted vault metadata.
    :param dry_run:
        Do not mutate the supplied database when ``True``.
    :param target:
        Optional reviewed chain/address pair; otherwise retain the original
        three-sftUSD migration scope. An explicit missing target is an error.
    :return:
        Counts for the targeted migration.
    :raises ValueError:
        If a reviewed cached row lacks compatible discovery metadata.
    """

    if target is not None:
        if (target.chain_id, target.vault_address.lower()) not in FLYING_TULIP_PROTOCOLS_BY_CHAIN:
            raise ValueError(f"Unreviewed Flying Tulip target: {target}")
        if target not in vault_db.rows:
            raise ValueError(f"Flying Tulip target is missing from metadata: {target}; run normal discovery first")
    targets = [target] if target is not None else [VaultSpec(chain_id, address) for chain_id, address in FLYING_TULIP_SFTUSD_BY_CHAIN.items()]
    present_targets = [spec for spec in targets if spec in vault_db.rows]
    replacements: dict[VaultSpec, VaultRow] = {}
    report_rows: list[tuple[int, str, str]] = []
    with tempfile.TemporaryDirectory(prefix="flying-tulip-metadata-token-cache-") as cache_directory:
        token_cache = TokenDiskCache(Path(cache_directory) / "tokens.sqlite")
        for spec in present_targets:
            row = vault_db.rows[spec]
            logger.info("Refreshing Flying Tulip metadata for %s (dry run: %s)", spec, dry_run)
            detection = row.get("_detection_data")
            if not isinstance(detection, ERC4262VaultDetection):
                raise ValueError(f"Flying Tulip target {spec} has no persisted ERC-4626 detection")
            if detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address.lower():
                raise ValueError(f"Flying Tulip target {spec} has mismatched persisted detection")
            web3 = create_multi_provider_web3(read_json_rpc_url(spec.chain_id))
            features = set(FLYING_TULIP_PROTOCOLS_BY_CHAIN[(spec.chain_id, spec.vault_address.lower())])
            refreshed_detection = dataclasses.replace(detection, features=features, updated_at=native_datetime_utc_now())
            rebuilt = create_vault_scan_record(web3, refreshed_detection, web3.eth.block_number, token_cache)
            if rebuilt.get("Protocol") != "Flying Tulip":
                raise ValueError(f"Flying Tulip target {spec} rebuilt with unexpected protocol {rebuilt.get('Protocol')!r}")
            if not rebuilt.get("Name") or rebuilt["Name"].startswith("<") or not rebuilt.get("Denomination") or rebuilt.get("NAV") is None:
                raise ValueError(f"Flying Tulip target {spec} returned incomplete metadata; refusing to replace the cached row")
            # Preserve manual enrichment but remove stale scanner-owned reward
            # fields when changing from the external-reward to stable variant.
            preserved = {key: value for key, value in row.items() if not key.startswith("_external_reward")}
            replacements[spec] = preserved | rebuilt
            report_rows.append((spec.chain_id, spec.vault_address, row.get("Protocol", "<missing>")))
    print(tabulate(report_rows, headers=("Chain", "Vault", "Previous protocol"), tablefmt="rounded_outline"))
    if not dry_run:
        vault_db.rows.update(replacements)
    return FlyingTulipMetadataMigrationResult(inspected_rows=len(present_targets), migrated_rows=len(replacements))


def main() -> None:
    """Run the targeted metadata migration using environment configuration.

    Apply mode holds the shared scanner lock across reading, validating,
    backing up and atomically writing the metadata pickle.

    :return:
        ``None`` after reporting the dry-run plan or saving a backed-up pickle.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(get_pipeline_data_dir() / "vault-metadata-db.pickle"))).expanduser().resolve()
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)
    dry_run = _parse_bool_env("DRY_RUN", default=True)
    chain_id = os.environ.get("CHAIN_ID")
    address = os.environ.get("VAULT_ADDRESS")
    if bool(chain_id) != bool(address):
        raise ValueError("CHAIN_ID and VAULT_ADDRESS must be supplied together")
    target = VaultSpec(int(chain_id), address) if chain_id and address else None
    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent / "scan-pipeline", timeout=60)
    with acquire_scanner_lifecycle_guard(vault_db_path.parent), lock:
        vault_db = VaultDatabase.read(vault_db_path)
        result = migrate_flying_tulip_metadata(vault_db, dry_run=dry_run, target=target)
        if dry_run:
            logger.info("Dry run: %s of %s cached Flying Tulip rows would be refreshed", result.migrated_rows, result.inspected_rows)
        elif result.migrated_rows:
            backup_path = _create_backup_path(vault_db_path)
            shutil.copy2(vault_db_path, backup_path)
            vault_db.write(vault_db_path)
            logger.info("Refreshed %s Flying Tulip rows. Backup: %s", result.migrated_rows, backup_path)
        else:
            logger.info("No cached Flying Tulip rows require migration")

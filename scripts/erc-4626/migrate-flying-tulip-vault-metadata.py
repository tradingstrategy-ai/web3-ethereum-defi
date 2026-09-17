#!/usr/bin/env python3
"""Refresh cached Flying Tulip sftUSD vault metadata without a chain reset.

The historical scanner may already contain the reviewed sftUSD vault rows from
generic ERC-4626 discovery. This targeted migration updates only those rows to
the Flying Tulip adapter, preserving all unrelated metadata, reader-state and
Parquet history. It never deletes a chain-wide cache or recreates discovery
history.

Usage::

    source .local-test.env && DRY_RUN=true poetry run python scripts/erc-4626/migrate-flying-tulip-vault-metadata.py
    source .local-test.env && DRY_RUN=false poetry run python scripts/erc-4626/migrate-flying-tulip-vault-metadata.py

Environment variables:

- ``VAULT_DB_PATH``: Optional metadata pickle path.
- ``DRY_RUN``: Report changes without writing; defaults to ``true``.
- ``JSON_RPC_ETHEREUM``, ``JSON_RPC_BINANCE`` and ``JSON_RPC_SONIC``: Required
  only for reviewed vault rows present in the metadata cache.
"""

import dataclasses
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_SFTUSD_BY_CHAIN
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow


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


def migrate_flying_tulip_metadata(vault_db: VaultDatabase, *, dry_run: bool) -> FlyingTulipMetadataMigrationResult:
    """Refresh only existing reviewed sftUSD rows in a metadata database.

    Each replacement preserves the original discovery counts and first-seen
    fields. Rows not yet discovered are left untouched for ordinary hardcoded
    lead discovery; this migration never manufactures activity evidence.

    :param vault_db:
        Existing persisted vault metadata.
    :param dry_run:
        Do not mutate the supplied database when ``True``.
    :return:
        Counts for the targeted migration.
    :raises ValueError:
        If a reviewed cached row lacks compatible discovery metadata.
    """

    targets = [VaultSpec(chain_id, address) for chain_id, address in FLYING_TULIP_SFTUSD_BY_CHAIN.items()]
    existing = [(spec, vault_db.rows[spec]) for spec in targets if spec in vault_db.rows]
    replacements: dict[VaultSpec, VaultRow] = {}
    report_rows: list[tuple[int, str, str]] = []
    with tempfile.TemporaryDirectory(prefix="flying-tulip-metadata-token-cache-") as cache_directory:
        token_cache = TokenDiskCache(Path(cache_directory) / "tokens.sqlite")
        for spec, row in existing:
            detection = row.get("_detection_data")
            if not isinstance(detection, ERC4262VaultDetection):
                raise ValueError(f"Flying Tulip target {spec} has no persisted ERC-4626 detection")
            if detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address.lower():
                raise ValueError(f"Flying Tulip target {spec} has mismatched persisted detection")
            web3 = create_multi_provider_web3(read_json_rpc_url(spec.chain_id))
            features = {ERC4626Feature.flying_tulip_like, ERC4626Feature.share_price_equivalence}
            refreshed_detection = dataclasses.replace(detection, features=features, updated_at=native_datetime_utc_now())
            rebuilt = create_vault_scan_record(web3, refreshed_detection, web3.eth.block_number, token_cache)
            if rebuilt.get("Protocol") != "Flying Tulip":
                raise ValueError(f"Flying Tulip target {spec} rebuilt with unexpected protocol {rebuilt.get('Protocol')!r}")
            replacements[spec] = rebuilt
            report_rows.append((spec.chain_id, spec.vault_address, row.get("Protocol", "<missing>")))
    print(tabulate(report_rows, headers=("Chain", "sftUSD", "Previous protocol"), tablefmt="rounded_outline"))
    if not dry_run:
        vault_db.rows.update(replacements)
    return FlyingTulipMetadataMigrationResult(inspected_rows=len(existing), migrated_rows=len(replacements))


def main() -> None:
    """Run the targeted metadata migration using environment configuration.

    :return:
        ``None`` after reporting the dry-run plan or saving a backed-up pickle.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)
    dry_run = _parse_bool_env("DRY_RUN", default=True)
    vault_db = VaultDatabase.read(vault_db_path)
    result = migrate_flying_tulip_metadata(vault_db, dry_run=dry_run)
    if dry_run:
        print(f"Dry run: {result.migrated_rows} of {result.inspected_rows} cached Flying Tulip rows would be refreshed; reader state and Parquet files are unchanged.")
        return
    if result.migrated_rows == 0:
        print("No cached Flying Tulip rows require migration.")
        return
    backup_path = _create_backup_path(vault_db_path)
    shutil.copy2(vault_db_path, backup_path)
    vault_db.write(vault_db_path)
    print(f"Refreshed {result.migrated_rows} Flying Tulip metadata rows. Backup: {backup_path}")


if __name__ == "__main__":
    main()

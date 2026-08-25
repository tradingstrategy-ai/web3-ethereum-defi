"""Repair metadata for the fixed Rysk Premium migration scope.

This is the current-metadata half of the one-off Rysk production migration.
It rebuilds exactly eight reviewed Ethereum and HyperEVM pool rows and their
lead records through the normal :class:`RyskVault` adapter. The fixed
deployment blocks provide the first-seen block and timestamp. Unrelated
metadata, all price files, contextual history, timestamp caches and reader
state remain unchanged.

Usage::

    # Default: perform real reads and report without writing
    poetry run python scripts/erc-4626/migrate-rysk-vault-metadata.py

    # Apply the exact reviewed migration
    DRY_RUN=false poetry run python scripts/erc-4626/migrate-rysk-vault-metadata.py

Environment variables:

- ``DRY_RUN``: report the complete repair without writing; defaults to
  ``true``.
- Standard Ethereum and HyperEVM RPC and logging environment variables are
  infrastructure configuration, not migration scope.
"""

import os
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.rysk.migration import RYSK_MIGRATION_CHAIN_IDS, RyskMigrationPool, iter_rysk_migration_pools, parse_rysk_migration_dry_run
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

RYSK_MIGRATION_FEATURES = {ERC4626Feature.rysk_premium_like, ERC4626Feature.share_price_equivalence}


@dataclass(slots=True, frozen=True)
class RyskMetadataMigrationResult:
    """Summarise the fixed-scope Rysk metadata repair.

    The counts distinguish new rows from existing rows rebuilt through the
    maintained Rysk adapter.
    """

    #: Reviewed rows constructed successfully.
    pools: int
    #: Rows absent from the input metadata database.
    inserted: int
    #: Existing target rows refreshed.
    updated: int


@dataclass(slots=True, frozen=True)
class RyskMetadataReplacement:
    """Hold one validated row and lead replacement before database mutation."""

    #: Common vault identity.
    spec: VaultSpec
    #: Rebuilt metadata merged over any existing row.
    row: VaultRow
    #: Lead record using the reviewed deployment boundary.
    lead: PotentialVaultMatch
    #: Human-readable ``insert`` or ``update`` action.
    action: str


def _read_activity_counts(existing: VaultRow | None, existing_lead: PotentialVaultMatch | None) -> tuple[int, int, int]:
    """Preserve previously observed activity while repairing metadata.

    :param existing:
        Existing target metadata row, when present.
    :param existing_lead:
        Existing target lead, when present.
    :return:
        Deposit, redemption and configuration event counts.
    """

    existing_detection = existing.get("_detection_data") if existing is not None else None
    if isinstance(existing_detection, ERC4262VaultDetection):
        return existing_detection.deposit_count, existing_detection.redeem_count, getattr(existing_detection, "configuration_count", 0)
    if existing_lead is not None:
        return existing_lead.deposit_count, existing_lead.withdrawal_count, existing_lead.configuration_count
    return 0, 0, 0


def _build_metadata_replacement(web3: Web3, pool: RyskMigrationPool, vault_db: VaultDatabase, token_cache: TokenDiskCache, metadata_block: int) -> RyskMetadataReplacement:
    """Build and validate one reviewed Rysk row without mutating the database.

    :param web3:
        Pool-chain connection.
    :param pool:
        Fixed migration target.
    :param vault_db:
        Metadata database used as the merge base.
    :param token_cache:
        Token metadata cache for common row construction.
    :param metadata_block:
        Fixed current-state block for this chain run.
    :return:
        Validated row and lead replacement.
    :raises RuntimeError:
        If the target does not rebuild as Rysk.
    """

    first_seen_at = native_datetime_utc_fromtimestamp(web3.eth.get_block(pool.deployment_block)["timestamp"])
    spec = VaultSpec(pool.chain_id, pool.address)
    existing = vault_db.rows.get(spec)
    deposit_count, redeem_count, configuration_count = _read_activity_counts(existing, vault_db.leads.get(spec))
    detection = ERC4262VaultDetection(
        chain=pool.chain_id,
        address=pool.address,
        first_seen_at_block=pool.deployment_block,
        first_seen_at=first_seen_at,
        features=set(RYSK_MIGRATION_FEATURES),
        updated_at=native_datetime_utc_now(),
        deposit_count=deposit_count,
        redeem_count=redeem_count,
        configuration_count=configuration_count,
    )
    rebuilt = create_vault_scan_record(web3, detection, metadata_block, token_cache)
    if rebuilt.get("Protocol") != "Rysk" or rebuilt.get("Name", "").startswith("<broken:"):
        raise RuntimeError(f"Rysk migration target {pool.chain_id}-{pool.address} rebuilt as {rebuilt.get('Protocol')!r} / {rebuilt.get('Name')!r}")

    merged = rebuilt if existing is None else existing | rebuilt
    lead = PotentialVaultMatch(
        chain=pool.chain_id,
        address=pool.address,
        first_seen_at_block=pool.deployment_block,
        first_seen_at=first_seen_at,
        deposit_count=deposit_count,
        withdrawal_count=redeem_count,
        configuration_count=configuration_count,
    )
    return RyskMetadataReplacement(spec=spec, row=merged, lead=lead, action="insert" if existing is None else "update")


def migrate_rysk_metadata(vault_db: VaultDatabase, token_cache: TokenDiskCache, *, dry_run: bool) -> RyskMetadataMigrationResult:
    """Build and optionally apply all reviewed Rysk metadata rows.

    Every RPC read and row validation completes before the in-memory database
    is mutated. Existing target dictionaries are the merge base so manual or
    future fields outside the adapter-owned scan record are retained.

    :param vault_db:
        Production metadata database or a test fixture.
    :param token_cache:
        Token metadata cache used by common scan-row construction.
    :param dry_run:
        Leave ``vault_db`` unchanged when ``True``.
    :return:
        Constructed, inserted and updated row counts.
    :raises RuntimeError:
        If an RPC has the wrong chain or any reviewed pool cannot be rebuilt as
        a valid Rysk row.
    """

    replacements: dict[VaultSpec, VaultRow] = {}
    replacement_leads: dict[VaultSpec, PotentialVaultMatch] = {}
    report_rows: list[tuple[int, str, int, str, str]] = []
    inserted = 0

    for chain_id in RYSK_MIGRATION_CHAIN_IDS:
        web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
        if web3.eth.chain_id != chain_id:
            raise RuntimeError(f"Rysk migration RPC returned chain ID {web3.eth.chain_id}, expected {chain_id}")
        metadata_block = web3.eth.block_number

        for pool in iter_rysk_migration_pools(chain_id):
            replacement = _build_metadata_replacement(web3, pool, vault_db, token_cache, metadata_block)
            if replacement.action == "insert":
                inserted += 1
            replacements[replacement.spec] = replacement.row
            replacement_leads[replacement.spec] = replacement.lead
            report_rows.append((chain_id, pool.address, pool.deployment_block, replacement.action, str(replacement.row["Name"])))

    print(tabulate(report_rows, headers=("Chain", "Pool", "Deployment block", "Action", "Name"), tablefmt="rounded_outline"))
    if not dry_run:
        vault_db.rows.update(replacements)
        vault_db.leads.update(replacement_leads)
    return RyskMetadataMigrationResult(pools=len(replacements), inserted=inserted, updated=len(replacements) - inserted)


def main() -> None:
    """Run the fixed Rysk metadata migration.

    Dry-run mode reads the production metadata pickle and real contracts but
    uses a temporary token cache. The persistent mode takes the shared scanner
    writer lock before reading or atomically replacing the metadata pickle.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_rysk_migration_dry_run(os.environ.get("DRY_RUN"))
    pipeline_dir = get_pipeline_data_dir()
    vault_db_path = pipeline_dir / "vault-metadata-db.pickle"
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)

    lock = nullcontext() if dry_run else wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60)
    with lock, tempfile.TemporaryDirectory(prefix="rysk-metadata-") as temporary_directory:
        vault_db = VaultDatabase.read(vault_db_path)
        token_cache_path = Path(temporary_directory) / "tokens.sqlite" if dry_run else TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH
        token_cache = TokenDiskCache(token_cache_path)
        try:
            result = migrate_rysk_metadata(vault_db, token_cache, dry_run=dry_run)
            if not dry_run:
                vault_db.write(vault_db_path)
                token_cache.commit()
        finally:
            token_cache.close()

    outcome = "Dry run; no files changed" if dry_run else f"Written atomically to {vault_db_path}"
    print(f"Rysk metadata migration: pools={result.pools}, inserted={result.inserted}, updated={result.updated}. {outcome}.")


if __name__ == "__main__":
    main()

"""Reconcile the reviewed Ethereum YieldBasis markets into vault metadata.

The migration is intentionally metadata-only. It validates the Factory and all
four reviewed market tuples, refreshes only YieldBasis rows, and normalises
their public display names. It never touches the common price Parquet,
contextual history or reader state. Set ``DRY_RUN=false`` for the atomic
metadata write; the default is a non-mutating preview.

Environment variables are infrastructure overrides only:

``DRY_RUN`` (default ``true``), ``VAULT_DATABASE``, ``TOKEN_CACHE``,
``JSON_RPC_ETHEREUM`` and ``LOG_LEVEL``.
"""

import os
import tempfile
from contextlib import nullcontext
from pathlib import Path

from tabulate import tabulate

from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS
from eth_defi.yield_basis.vault_catalog import fetch_yield_basis_scan_preparation
from eth_defi.yield_basis.vault_sync import fetch_and_sync_yield_basis_vault_catalogue


def count_legacy_market_display_names(vault_database: VaultDatabase) -> int:
    """Count reviewed records whose display name still exposes a Factory ID.

    YieldBasis Factory market IDs remain in private catalogue metadata for
    troubleshooting, but are no longer part of public vault names. The count
    makes the one-off migration's intended name repair visible in dry-run and
    persistent output.

    :param vault_database:
        In-memory metadata cache before or after catalogue reconciliation.
    :return:
        Number of reviewed rows named with the legacy ``· market <ID>`` suffix.
    """

    return sum(vault_database.rows.get(VaultSpec(1, review.lt_address.lower()), {}).get("Name") == f"yb-LP {review.asset_symbol} · market {review.market_id}" for review in YIELD_BASIS_ACTIVE_MARKETS.values())


def migrate_metadata(*, database_path: Path, token_cache_path: Path, dry_run: bool) -> tuple[int, int, int, int]:
    """Validate and reconcile the four reviewed Ethereum markets and names.

    :param database_path:
        Metadata pickle to read and, in persistent mode, atomically replace.
    :param token_cache_path:
        Token cache used for the Factory and LT metadata reads.
    :param dry_run:
        Keep the metadata database and token cache unchanged when true.
    :return:
        Product, insertion, update and legacy-name repair counts.
    """

    web3 = create_multi_provider_web3(read_json_rpc_url(1))
    block_number = get_almost_latest_block_number(web3)
    vault_database = VaultDatabase.read(database_path) if database_path.exists() else VaultDatabase()
    renamed = count_legacy_market_display_names(vault_database)
    token_cache = TokenDiskCache(token_cache_path)
    try:
        preparation = fetch_yield_basis_scan_preparation(web3, block_number)
        if not preparation.factory_valid:
            raise RuntimeError("YieldBasis Factory validation failed: " + "; ".join(preparation.review_required))
        if preparation.review_required:
            raise RuntimeError("YieldBasis market review required: " + "; ".join(preparation.review_required))
        result = fetch_and_sync_yield_basis_vault_catalogue(
            web3=web3,
            vault_db=vault_database,
            token_cache=token_cache,
            block_number=block_number,
            preparation=preparation,
        )
        assert count_legacy_market_display_names(vault_database) == 0, "YieldBasis catalogue reconciliation left legacy market-ID display names"
        if not dry_run:
            # Commit token metadata first.  VaultDatabase.write() is atomic,
            # so a cache failure cannot leave a newly published row referring
            # to metadata that was never persisted.
            token_cache.commit()
            database_path.parent.mkdir(parents=True, exist_ok=True)
            vault_database.write(database_path)
        return result.products, result.inserted, result.updated, renamed
    finally:
        token_cache.close()


def main() -> None:
    """Run the reviewed metadata-only migration."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    pipeline_dir = get_pipeline_data_dir()
    database_path = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    temporary_directory = None
    if dry_run:
        temporary_directory = tempfile.TemporaryDirectory(prefix="yield-basis-metadata-")
        token_cache_path = Path(temporary_directory.name) / "tokens.sqlite"
    else:
        token_cache_path = Path(os.environ.get("TOKEN_CACHE", TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH)).expanduser()

    lock = nullcontext() if dry_run else wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60)
    try:
        with lock:
            products, inserted, updated, renamed = migrate_metadata(
                database_path=database_path,
                token_cache_path=token_cache_path,
                dry_run=dry_run,
            )
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()

    print(
        tabulate(
            [(1, products, inserted, updated, renamed)],
            headers=("Chain ID", "Products", "Inserted", "Updated", "Names renamed"),
            tablefmt="rounded_outline",
        ),
    )
    print(f"{'Dry run; not written' if dry_run else 'Written'}: {database_path}")


if __name__ == "__main__":
    main()

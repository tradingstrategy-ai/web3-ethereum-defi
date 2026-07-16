#!/usr/bin/env python3
"""Migrate existing Midas metadata rows to on-chain payment-token denominations.

The Midas adapter historically exported synthetic off-chain USD for every
product. The adapter now reads the first non-zero entry of the issuance vault's
``getPaymentTokens()`` list as its primary payment token and exposes it through
the :class:`eth_defi.vault.base.VaultBase` denomination-token API. Existing
``vault-metadata-db.pickle`` rows must therefore be refreshed once; normal
scanner runs will use the new denomination for future metadata scans.

This is metadata-only and deliberately does **not** alter raw or cleaned price
Parquet files, reader state, leads, or a chain's scanner cursor. Price Parquet
does not store denomination metadata, and changing a scanner cursor could make
the main pipeline skip unrelated chain data. The script changes only
``Denomination``, ``_denomination_token`` and ``_synthetic_usd_denomination``
on existing Midas rows. It makes a timestamped backup before its atomic
metadata-database write.

The script starts in dry-run mode. First inspect the proposed changes, then set
``DRY_RUN=false`` to apply them:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/midas/migrate-payment-token-denominations.py
    source .local-test.env && DRY_RUN=false poetry run python scripts/midas/migrate-payment-token-denominations.py

Configuration is through environment variables:

``DRY_RUN``
    Print proposed changes without writing. Defaults to ``true``.

``NETWORKS``
    Optional comma-separated chain ids or names, e.g. ``1,ethereum,base``.

``PRODUCTS``
    Optional comma-separated Midas product symbols, e.g. ``mTBILL,mBASIS``.

``MAX_WORKERS``
    Concurrent RPC reads. Defaults to ``8``.

``VAULT_DB_PATH``
    Metadata database path. Defaults to the active pipeline data directory.

``BACKUP_PATH``
    Optional backup pickle path. By default a timestamped sibling of
    ``VAULT_DB_PATH`` is created before a non-dry-run migration.

``LOG_LEVEL``
    Python logging level. Defaults to ``info``.
"""

import logging
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from eth_typing import HexAddress
from joblib import Parallel, delayed
from tabulate import tabulate
from web3 import Web3

from eth_defi.chain import CHAIN_NAMES
from eth_defi.compat import native_datetime_utc_now
from eth_defi.midas.constants import MIDAS_PRODUCTS, MidasProduct
from eth_defi.midas.vault import MidasVault, export_midas_usd_denomination
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DenominationMigration:
    """One proposed Midas metadata denomination update."""

    #: Existing vault database key.
    vault_spec: VaultSpec

    #: Product used to construct the Midas adapter.
    product: MidasProduct

    #: Existing scanner denomination symbol.
    old_symbol: str | None

    #: Existing scanner denomination ERC-20 address, if any.
    old_address: HexAddress | None

    #: Primary Midas payment-token symbol, or off-chain USD.
    new_symbol: str

    #: Primary Midas payment-token address, or ``None`` for off-chain USD.
    new_address: HexAddress | None

    #: Export-ready denomination metadata.
    denomination_token: dict[str, object]

    #: Whether the replacement denomination is synthetic off-chain USD.
    synthetic_usd_denomination: bool

    @property
    def changed(self) -> bool:
        """Return whether the exported denomination identity will change.

        The migration intentionally compares the human-readable symbol and
        ERC-20 address only. Other token export fields such as total supply are
        live diagnostics and must not trigger a metadata-only rewrite.

        :return:
            ``True`` when the existing row needs a denomination update.
        """

        return self.old_symbol != self.new_symbol or self.old_address != self.new_address


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value to use when the variable is unset.
    :return:
        Parsed truth value.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_csv_env(name: str) -> set[str] | None:
    """Parse optional comma-separated environment selectors.

    :param name:
        Environment variable name.
    :return:
        Lower-case selectors, or ``None`` when the variable is unset.
    """

    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def get_chain_selectors(chain_id: int) -> set[str]:
    """Return selectors that identify a chain in ``NETWORKS``.

    :param chain_id:
        EVM chain id.
    :return:
        Decimal chain id and, when known, lower-case chain name.
    """

    selectors = {str(chain_id)}
    chain_name = CHAIN_NAMES.get(chain_id)
    if chain_name:
        selectors.add(chain_name.lower())
    return selectors


def iter_selected_products() -> Iterator[MidasProduct]:
    """Iterate unique Midas adapter products selected by the environment.

    The migration uses the same ``MIDAS_PRODUCTS`` registry as the adapter, so
    it cannot write a denomination for a product unsupported by the scanner.

    :return:
        Selected products in registry order.
    """

    networks = parse_csv_env("NETWORKS")
    products = parse_csv_env("PRODUCTS")
    seen: set[tuple[int, HexAddress]] = set()

    for product in MIDAS_PRODUCTS.values():
        key = (product.chain_id, product.token)
        if key in seen:
            continue
        seen.add(key)

        if networks and not (get_chain_selectors(product.chain_id) & networks):
            continue
        if products and product.symbol.lower() not in products:
            continue
        yield product


def resolve_vault_database_path() -> Path:
    """Resolve the metadata database targeted by the migration.

    :return:
        Explicit ``VAULT_DB_PATH`` or the active pipeline metadata database.
    """

    configured_path = os.environ.get("VAULT_DB_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return get_pipeline_data_dir() / "vault-metadata-db.pickle"


def resolve_backup_path(vault_db_path: Path) -> Path:
    """Resolve the one-time backup filename for a real migration.

    :param vault_db_path:
        Metadata database to back up.
    :return:
        Explicit ``BACKUP_PATH`` or a timestamped sibling path.
    """

    configured_path = os.environ.get("BACKUP_PATH")
    if configured_path:
        return Path(configured_path).expanduser()

    timestamp = native_datetime_utc_now().strftime("%Y%m%d-%H%M%S")
    return vault_db_path.with_name(f"{vault_db_path.stem}.before-midas-payment-token-migration-{timestamp}{vault_db_path.suffix}")


def fetch_denomination_migration(web3: Web3, product: MidasProduct, existing_row: VaultRow) -> DenominationMigration:
    """Read a primary payment token and construct one metadata-only update.

    The Midas adapter owns the ``getPaymentTokens()`` ABI call and zero-address
    manual-fulfilment handling. Delegating to it keeps this one-off migration
    exactly aligned with future normal scanner behaviour.

    :param web3:
        JSON-RPC connection for the product chain.
    :param product:
        Midas product from the adapter registry.
    :param existing_row:
        Current vault metadata row to compare.
    :return:
        Proposed denomination update without mutating the database.
    """

    vault_spec = VaultSpec(chain_id=product.chain_id, vault_address=product.token)
    vault = MidasVault(web3, vault_spec, token_cache={})
    token: TokenDetails | None = vault.fetch_denomination_token()
    existing_token = existing_row.get("_denomination_token") or {}

    if token is None:
        new_symbol = "USD"
        new_address = None
        token_export = export_midas_usd_denomination(product.chain_id)
    else:
        if token.symbol is None:
            message = f"Primary Midas payment token {token.address} for {product.symbol} has no symbol"
            raise RuntimeError(message)
        new_symbol = token.symbol
        new_address = token.address
        token_export = token.export()

    old_address = existing_token.get("address")
    if old_address is not None:
        old_address = HexAddress(old_address)

    return DenominationMigration(
        vault_spec=vault_spec,
        product=product,
        old_symbol=existing_row.get("Denomination"),
        old_address=old_address,
        new_symbol=new_symbol,
        new_address=new_address,
        denomination_token=token_export,
        synthetic_usd_denomination=token is None,
    )


def apply_migrations(vault_db: VaultDatabase, migrations: list[DenominationMigration]) -> None:
    """Apply only denomination fields to in-memory existing metadata rows.

    No rows are created and no scanner state is touched. This deliberately
    preserves all unrelated metadata collected since the last Midas scan.

    :param vault_db:
        Metadata database loaded from disk.
    :param migrations:
        Changed migration entries to apply.
    :return:
        ``None`` after mutating ``vault_db`` in memory.
    """

    for migration in migrations:
        row = vault_db.rows[migration.vault_spec].copy()
        row["Denomination"] = migration.new_symbol
        row["_denomination_token"] = migration.denomination_token
        row["_synthetic_usd_denomination"] = migration.synthetic_usd_denomination
        vault_db.rows[migration.vault_spec] = row


def main() -> None:
    """Run the metadata-only Midas denomination migration.

    Reads all selected records before taking a backup or writing, so an RPC or
    token-metadata failure leaves the production database unchanged.

    :return:
        ``None`` after displaying the plan and optionally writing metadata.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=True)
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    if max_workers < 1:
        message = "MAX_WORKERS must be at least one"
        raise ValueError(message)

    vault_db_path = resolve_vault_database_path()
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault metadata database does not exist: {vault_db_path}")
    vault_db = VaultDatabase.read(vault_db_path)

    selected_products = list(iter_selected_products())
    existing_products = [product for product in selected_products if VaultSpec(product.chain_id, product.token) in vault_db.rows]
    missing_products = [product for product in selected_products if VaultSpec(product.chain_id, product.token) not in vault_db.rows]
    if not existing_products:
        message = "No selected Midas products have existing metadata rows to migrate"
        raise RuntimeError(message)

    web3_by_chain = {chain_id: create_multi_provider_web3(read_json_rpc_url(chain_id), retries=2, hint="Midas payment-token denomination migration") for chain_id in {product.chain_id for product in existing_products}}
    migrations = Parallel(n_jobs=max_workers, backend="threading")(
        delayed(fetch_denomination_migration)(
            web3_by_chain[product.chain_id],
            product,
            vault_db.rows[VaultSpec(product.chain_id, product.token)],
        )
        for product in existing_products
    )
    changes = [migration for migration in migrations if migration.changed]

    print(
        tabulate(
            [
                [
                    migration.product.chain_id,
                    migration.product.symbol,
                    migration.old_symbol,
                    migration.old_address or "-",
                    migration.new_symbol,
                    migration.new_address or "-",
                    "change" if migration.changed else "unchanged",
                ]
                for migration in migrations
            ],
            headers=["chain", "product", "old symbol", "old token", "new symbol", "new token", "status"],
            tablefmt="rounded_outline",
        )
    )
    print(f"\nSelected existing Midas rows: {len(existing_products)}")
    print(f"Rows requiring denomination migration: {len(changes)}")
    if missing_products:
        print(f"Registry products without existing rows (not created): {len(missing_products)}")

    if dry_run:
        print("Dry run: no files written. Re-run with DRY_RUN=false to apply these changes.")
        return
    if not changes:
        print("No migration needed: metadata database already uses current payment-token denominations.")
        return

    backup_path = resolve_backup_path(vault_db_path)
    if backup_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault_db_path, backup_path)
    apply_migrations(vault_db, changes)
    vault_db.write(vault_db_path)
    print(f"Migrated {len(changes)} Midas metadata rows in {vault_db_path}")
    print(f"Backup written to {backup_path}")


if __name__ == "__main__":
    main()

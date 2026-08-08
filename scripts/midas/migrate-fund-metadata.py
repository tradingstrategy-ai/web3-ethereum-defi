#!/usr/bin/env python3
"""Repair Midas tokenised-fund metadata in an existing vault database.

The Midas registry now holds reviewed fund classification and display metadata.
Rows already present in ``vault-metadata-db.pickle`` do not receive those
values until a full rescan, so this targeted migration updates only existing
Midas products explicitly classified as tokenised funds. It does not create
rows or alter leads, scanner cursors, reader state, or price Parquet files.

The script starts in dry-run mode. Inspect the proposed updates, then apply
them explicitly:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/midas/migrate-fund-metadata.py
    source .local-test.env && DRY_RUN=false poetry run python scripts/midas/migrate-fund-metadata.py

Configuration is through environment variables:

``DRY_RUN``
    Print proposed changes without writing. Defaults to ``true``.

``NETWORKS``
    Optional comma-separated chain ids or names, e.g. ``1,ethereum,base``.

``PRODUCTS``
    Optional comma-separated Midas product symbols, e.g. ``mTBILL``.

``VAULT_DB_PATH``
    Metadata database path. Defaults to the active pipeline data directory.

``BACKUP_PATH``
    Optional backup pickle path. By default a timestamped sibling of
    ``VAULT_DB_PATH`` is created before a non-dry-run migration.
"""

import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.chain import CHAIN_NAMES
from eth_defi.compat import native_datetime_utc_now
from eth_defi.midas.constants import MIDAS_PRODUCTS, MidasProduct
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir


@dataclass(slots=True, frozen=True)
class FundMetadataMigration:
    """One proposed Midas tokenised-fund metadata update.

    :param vault_spec:
        Existing vault database key.
    :param product:
        Reviewed Midas product metadata.
    :param updates:
        Replacement values for only the metadata fields owned by this migration.
    :param changed_fields:
        Fields whose existing database values differ from the reviewed values.
    """

    vault_spec: VaultSpec
    product: MidasProduct
    updates: dict[str, object]
    changed_fields: tuple[str, ...]

    @property
    def changed(self) -> bool:
        """Return whether this row needs a metadata update.

        :return:
            ``True`` when at least one Midas-owned field differs.
        """

        return bool(self.changed_fields)


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


def iter_selected_fund_products() -> Iterator[MidasProduct]:
    """Iterate selected reviewed Midas tokenised-fund products.

    The registry supplies the classification, so this migration cannot
    accidentally classify every Midas strategy product as a fund.

    :return:
        Unique reviewed fund products in registry order.
    """

    networks = parse_csv_env("NETWORKS")
    products = parse_csv_env("PRODUCTS")
    seen: set[tuple[int, str]] = set()

    for product in MIDAS_PRODUCTS.values():
        key = (product.chain_id, product.token)
        if key in seen or not product.is_tokenised_fund:
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
    return vault_db_path.with_name(f"{vault_db_path.stem}.before-midas-fund-metadata-migration-{timestamp}{vault_db_path.suffix}")


def create_fund_metadata_migration(product: MidasProduct, existing_row: VaultRow) -> FundMetadataMigration:
    """Construct a metadata-only repair for one reviewed Midas fund row.

    :param product:
        Registry product marked as a tokenised fund.
    :param existing_row:
        Current vault metadata row to compare.
    :return:
        Proposed update without mutating the database.
    :raise ValueError:
        If a product marked as a fund lacks its required reviewed display data.
    """

    if not product.is_tokenised_fund:
        raise ValueError(f"Midas product is not a tokenised fund: {product.symbol}")
    if not product.short_description or not product.description or not product.product_link:
        raise ValueError(f"Reviewed Midas fund metadata is incomplete: {product.symbol}")

    updates = {
        "_flags": set(existing_row.get("_flags", set())) | {VaultFlag.tokenised_fund},
        "_short_description": product.short_description,
        "_description": product.description,
        "Link": product.product_link,
    }
    changed_fields = tuple(field for field, value in updates.items() if existing_row.get(field) != value)

    return FundMetadataMigration(
        vault_spec=VaultSpec(chain_id=product.chain_id, vault_address=product.token),
        product=product,
        updates=updates,
        changed_fields=changed_fields,
    )


def apply_migrations(vault_db: VaultDatabase, migrations: list[FundMetadataMigration]) -> None:
    """Apply fund metadata updates to existing rows in memory.

    :param vault_db:
        Metadata database loaded from disk.
    :param migrations:
        Changed migration entries to apply.
    :return:
        ``None`` after updating only the selected existing rows.
    """

    for migration in migrations:
        row = vault_db.rows[migration.vault_spec].copy()
        row.update(migration.updates)
        vault_db.rows[migration.vault_spec] = row


def main() -> None:
    """Run the metadata-only Midas tokenised-fund migration.

    The migration is idempotent: a row that already has the reviewed flag,
    descriptions and link is reported as unchanged. A backup is created only
    immediately before a real write.

    :return:
        ``None`` after displaying the plan and optionally writing metadata.
    """

    dry_run = parse_bool_env("DRY_RUN", default=True)
    vault_db_path = resolve_vault_database_path()
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault metadata database does not exist: {vault_db_path}")

    vault_db = VaultDatabase.read(vault_db_path)
    selected_products = list(iter_selected_fund_products())
    existing_products = [product for product in selected_products if VaultSpec(product.chain_id, product.token) in vault_db.rows]
    missing_products = [product for product in selected_products if VaultSpec(product.chain_id, product.token) not in vault_db.rows]
    if not existing_products:
        message = "No selected Midas fund products have existing metadata rows to migrate"
        raise RuntimeError(message)

    migrations = [create_fund_metadata_migration(product, vault_db.rows[VaultSpec(product.chain_id, product.token)]) for product in existing_products]
    changes = [migration for migration in migrations if migration.changed]

    print(
        tabulate(
            [
                [
                    migration.product.chain_id,
                    migration.product.symbol,
                    ", ".join(migration.changed_fields) or "unchanged",
                ]
                for migration in migrations
            ],
            headers=["chain", "product", "metadata update"],
            tablefmt="rounded_outline",
        )
    )
    print(f"\nSelected existing Midas fund rows: {len(existing_products)}")
    print(f"Rows requiring metadata migration: {len(changes)}")
    if missing_products:
        print(f"Registry fund products without existing rows (not created): {len(missing_products)}")

    if dry_run:
        print("Dry run: no files written. Re-run with DRY_RUN=false to apply these changes.")
        return
    if not changes:
        print("No migration needed: metadata database already uses the reviewed Midas fund metadata.")
        return

    backup_path = resolve_backup_path(vault_db_path)
    if backup_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault_db_path, backup_path)
    apply_migrations(vault_db, changes)
    vault_db.write(vault_db_path)
    print(f"Migrated {len(changes)} Midas fund metadata rows in {vault_db_path}")
    print(f"Backup written to {backup_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Remove invalid zero-GAV Enzyme Blue price observations.

The first Enzyme Blue historical integration used ``calcGav()`` as its share
valuation source.  A historical Blue release can return zero GAV while its
VaultProxy reports outstanding shares.  This is not a usable investor price:
the shared max-drawdown calculation interprets it as a complete loss.

This repair removes only those explicitly invalid raw observations for
factory-confirmed Enzyme Blue vaults, preserves an immutable copy of the raw
Parquet file, and atomically rebuilds the affected cleaned histories.  It does
not alter real zero-supply bootstrap rows, Onyx vaults, or any unrelated
protocol.

Usage::

    source .local-test.env && DRY_RUN=false ENZYME_ZERO_GAV_REPAIR_CONFIRM=true \\
        poetry run python scripts/enzyme/repair-zero-gav-history.py

Environment variables:

- ``DRY_RUN``: inspect affected rows without writing, default ``true``.
- ``ENZYME_ZERO_GAV_REPAIR_CONFIRM``: must be ``true`` with ``DRY_RUN=false``.
- ``ENZYME_REPAIR_CLEAN_PRICES``: rebuild selected cleaned histories, default
  ``true``.
- ``VAULT_DB_PATH``, ``UNCLEANED_PRICE_DATABASE`` and
  ``CLEANED_PRICE_DATABASE``: optional pipeline state paths.
- ``PIPELINE_LOCK_TIMEOUT``: shared writer-lock timeout in seconds, default
  ``60``.
"""

import os
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from filelock import Timeout as FileLockTimeout
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec, verify_parquet_file
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase


@dataclass(slots=True)
class ZeroGavRepairReport:
    """Summarise the Enzyme zero-GAV repair selection.

    #: Number of factory-confirmed Blue vaults selected from metadata.
    """

    selected_vault_count: int

    #: Number of raw rows with zero share price and positive share supply.
    invalid_row_count: int

    #: Number of selected Blue vaults that contain an invalid raw row.
    affected_vault_count: int

    #: Invalid-row count by EVM chain id.
    invalid_rows_by_chain: dict[int, int]

    #: Timestamped raw-Parquet backup path, absent for a dry run.
    backup_path: Path | None = None


def parse_boolean_env(name: str, *, default: bool) -> bool:
    """Read a strict boolean environment variable.

    The repair must be explicit in production.  Accepting the standard common
    spellings makes operational invocation ergonomic while rejecting typos
    prevents an accidental destructive run.

    :param name: Environment variable name.
    :param default: Value used when the variable is absent.
    :return: Parsed boolean value.
    :raise ValueError: If the configured value is not a recognised boolean.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {value!r}")


def parse_path_env(name: str, default: Path) -> Path:
    """Resolve one optional production-state path.

    :param name: Environment variable name.
    :param default: Repository-standard production path.
    :return: Expanded configured path.
    """

    return Path(os.environ.get(name, str(default))).expanduser()


def fetch_enzyme_blue_specs(vault_database: VaultDatabase) -> set[VaultSpec]:
    """Return factory-qualified Enzyme Blue vault identities from metadata.

    The persisted detection feature is the migration's authoritative protocol
    discriminator.  Restricting this repair to it avoids deleting a real zero
    price from any generic ERC-4626 or Enzyme Onyx product.

    :param vault_database: Loaded shared vault metadata database.
    :return: Lower-case Enzyme Blue vault specifications.
    """

    return {spec for spec, row in vault_database.rows.items() if ERC4626Feature.enzyme_blue_like in row["_detection_data"].features}


def create_zero_gav_mask(table: pa.Table, selected_specs: set[VaultSpec]) -> pa.Array:
    """Identify invalid Blue rows in one raw-Parquet record batch.

    A valid Blue price is unavailable when GAV is zero but the supply is
    positive.  The raw scanner records the same condition as a zero share
    price and zero total assets; checking all three columns prevents a generic
    zero-valued field from being removed accidentally.

    :param table: Raw vault-price rows with canonical scanner columns.
    :param selected_specs: Factory-confirmed Blue vault identities.
    :return: Boolean Arrow mask selecting only invalid Blue observations.
    """

    required_columns = {"chain", "address", "share_price", "total_assets", "total_supply"}
    missing_columns = required_columns - set(table.column_names)
    if missing_columns:
        raise ValueError(f"Raw price data is missing columns required for Enzyme GAV repair: {sorted(missing_columns)}")

    zero_gav_mask = pc.and_(
        pc.equal(table["share_price"], 0),
        pc.and_(pc.equal(table["total_assets"], 0), pc.greater(table["total_supply"], 0)),
    )
    candidate_positions = pc.indices_nonzero(zero_gav_mask).to_pylist()
    if not candidate_positions:
        return pa.array([False] * len(table))

    # Production metadata contains thousands of Blue vaults.  First use Arrow
    # to narrow each record batch to the rare zero-GAV shape, then resolve the
    # exact chain-and-address identity in Python.  Constructing thousands of
    # Arrow ``or`` expressions per batch made a whole-file dry run impractical.
    selected_pairs = {(spec.chain_id, spec.vault_address.lower()) for spec in selected_specs}
    candidate_table = table.take(pa.array(candidate_positions, type=pa.int64()))
    invalid_positions = [position for position, chain_id, address in zip(candidate_positions, candidate_table["chain"].to_pylist(), candidate_table["address"].to_pylist(), strict=True) if (int(chain_id), address.lower()) in selected_pairs]
    invalid_position_set = set(invalid_positions)
    return pa.array([position in invalid_position_set for position in range(len(table))])


def create_backup_path(raw_price_path: Path) -> Path:
    """Create a collision-resistant sibling backup path.

    :param raw_price_path: Existing production raw-price Parquet path.
    :return: Non-existing dated backup path in the same directory.
    :raise FileExistsError: If a clock-collision would overwrite a backup.
    """

    timestamp = native_datetime_utc_now().strftime("%Y%m%dT%H%M%SZ")
    backup_path = raw_price_path.with_name(f"{raw_price_path.stem}.before-enzyme-zero-gav-repair-{timestamp}{raw_price_path.suffix}")
    if backup_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing Enzyme repair backup: {backup_path}")
    return backup_path


def repair_zero_gav_rows(
    raw_price_path: Path,
    selected_specs: set[VaultSpec],
    *,
    dry_run: bool,
) -> tuple[ZeroGavRepairReport, set[str]]:
    """Delete zero-GAV Blue rows by streaming a verified Parquet replacement.

    The original raw file remains untouched until a complete replacement has
    been written and validated.  A non-dry run copies the original beside the
    source before the atomic replacement, making the removed source samples
    recoverable for later valuation research.

    :param raw_price_path: Existing raw vault-price Parquet file.
    :param selected_specs: Factory-confirmed Enzyme Blue vault identities.
    :param dry_run: Report candidates without creating a backup or replacement.
    :return: Repair report and canonical ids of affected vault histories.
    :raise FileNotFoundError: If the raw Parquet source is absent.
    """

    if not raw_price_path.exists():
        raise FileNotFoundError(f"Raw price database does not exist: {raw_price_path}")
    if not selected_specs:
        message = "No Enzyme Blue vaults selected; refusing to inspect or rewrite the raw price database"
        raise ValueError(message)

    reader = pq.ParquetFile(raw_price_path)
    total_rows = 0
    invalid_row_count = 0
    invalid_rows_by_chain: Counter[int] = Counter()
    affected_ids: set[str] = set()
    temp_path: str | None = None
    writer: pq.ParquetWriter | None = None

    try:
        if not dry_run:
            temp_fd, temp_path = tempfile.mkstemp(suffix=".parquet", dir=str(raw_price_path.parent))
            os.close(temp_fd)
            writer = pq.ParquetWriter(temp_path, reader.schema_arrow, compression="zstd")

        for batch in reader.iter_batches(batch_size=100_000):
            table = pa.Table.from_batches([batch], schema=reader.schema_arrow)
            invalid_mask = create_zero_gav_mask(table, selected_specs)
            invalid_count = int(pc.sum(pc.cast(invalid_mask, pa.int64())).as_py() or 0)
            total_rows += len(table)
            if invalid_count:
                invalid_table = table.filter(invalid_mask)
                invalid_row_count += invalid_count
                for chain_id, address in zip(invalid_table["chain"].to_pylist(), invalid_table["address"].to_pylist(), strict=True):
                    invalid_rows_by_chain[int(chain_id)] += 1
                    affected_ids.add(VaultSpec(int(chain_id), address).as_string_id())
            if writer is not None:
                writer.write_table(table.filter(pc.invert(invalid_mask)))

        if writer is not None:
            writer.close()
            writer = None
            verify_parquet_file(temp_path, expected_rows=total_rows - invalid_row_count, required_columns=list(reader.schema_arrow.names))
            backup_path = create_backup_path(raw_price_path)
            shutil.copy2(raw_price_path, backup_path)
            os.replace(temp_path, raw_price_path)
            temp_path = None
        else:
            backup_path = None
    except BaseException:
        if writer is not None:
            writer.close()
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    report = ZeroGavRepairReport(
        selected_vault_count=len(selected_specs),
        invalid_row_count=invalid_row_count,
        affected_vault_count=len(affected_ids),
        invalid_rows_by_chain=dict(sorted(invalid_rows_by_chain.items())),
        backup_path=backup_path,
    )
    return report, affected_ids


def run_repair() -> None:
    """Run the confirmed zero-GAV repair under the shared pipeline lock.

    The cleaner is run only after a successful raw replacement, and only for
    the identities whose invalid rows were removed.  This limits both the
    production write scope and the recovery surface.

    :return: ``None``.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/enzyme-zero-gav-repair.log"))
    dry_run = parse_boolean_env("DRY_RUN", default=True)
    clean_prices = parse_boolean_env("ENZYME_REPAIR_CLEAN_PRICES", default=True)
    confirmed = parse_boolean_env("ENZYME_ZERO_GAV_REPAIR_CONFIRM", default=False)
    if not dry_run and not confirmed:
        message = "Set ENZYME_ZERO_GAV_REPAIR_CONFIRM=true to remove raw Enzyme observations"
        raise RuntimeError(message)

    vault_database_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    raw_price_path = parse_path_env("UNCLEANED_PRICE_DATABASE", DEFAULT_UNCLEANED_PRICE_DATABASE)
    cleaned_price_path = parse_path_env("CLEANED_PRICE_DATABASE", DEFAULT_RAW_PRICE_DATABASE)
    lock_timeout = float(os.environ.get("PIPELINE_LOCK_TIMEOUT", "60"))
    pipeline_lock_path = vault_database_path.parent / "scan-pipeline"

    with wait_other_writers(pipeline_lock_path, timeout=lock_timeout):
        vault_database = VaultDatabase.read(vault_database_path)
        report, affected_ids = repair_zero_gav_rows(raw_price_path, fetch_enzyme_blue_specs(vault_database), dry_run=dry_run)
        print("Enzyme zero-GAV repair plan")
        print(tabulate([{"selected Blue vaults": report.selected_vault_count, "invalid rows": report.invalid_row_count, "affected vaults": report.affected_vault_count, "dry run": dry_run}], headers="keys", tablefmt="github"))
        print(tabulate([{"chain": chain_id, "invalid rows": count} for chain_id, count in report.invalid_rows_by_chain.items()], headers="keys", tablefmt="github"))
        if report.backup_path is not None:
            print(f"Raw price backup: {report.backup_path}")

        if not dry_run and clean_prices and affected_ids:
            cleaned_rows = replace_cleaned_vault_histories(
                affected_ids,
                vault_db_path=vault_database_path,
                raw_price_df_path=raw_price_path,
                cleaned_price_df_path=cleaned_price_path,
                require_all_cleaned=False,
            )
            print(f"Rebuilt {cleaned_rows:,} cleaned Enzyme Blue price rows")


def main() -> None:
    """Provide a command-line entry point for the repair.

    :return: ``None``.
    """

    run_repair()


if __name__ == "__main__":
    try:
        main()
    except FileLockTimeout:
        message = "Vault scan pipeline is locked by another scanner; retry after it finishes"
        raise SystemExit(message) from None
    except KeyboardInterrupt:
        sys.exit(130)

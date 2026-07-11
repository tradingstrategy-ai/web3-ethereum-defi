"""Fix stale HLP deposit-open markers in cached parquet data.

The Hyperliquidity Provider (HLP) is a Hyperliquid protocol vault
(``relationship_type="parent"``), not a legacy user-created leader vault.
Older exports could still apply the legacy 5% leader-fraction warning to HLP
history rows and mark deposits as closed.

This script repairs already-written parquet files without re-scanning:

.. code-block:: shell

    # Fix default raw and cleaned pipeline parquet files
    poetry run python scripts/hyperliquid/fix-hlp-deposit-open-marker.py

    # Dry run
    DRY_RUN=true poetry run python scripts/hyperliquid/fix-hlp-deposit-open-marker.py

    # Fix specific files
    PARQUET_PATHS=/tmp/vault-prices-1h.parquet,/tmp/cleaned-vault-prices-1h.parquet \\
      poetry run python scripts/hyperliquid/fix-hlp-deposit-open-marker.py

Environment variables:

- ``PARQUET_PATHS``: Comma-separated parquet files to repair.
  Default: ``~/.tradingstrategy/vaults/vault-prices-1h.parquet`` and
  ``~/.tradingstrategy/vaults/cleaned-vault-prices-1h.parquet`` when present.
- ``VAULT_ADDRESS``: Vault address to repair.
  Default: HLP mainnet address.
- ``DRY_RUN``: If ``true``, report rows that would change. Default: ``false``.
- ``BACKUP``: If ``true``, write ``.bak-YYYYmmdd-HHMMSS`` backups before
  modifying files. Default: ``true``.
- ``LOG_LEVEL``: Logging level. Default: ``info``.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.constants import HLP_VAULT_ADDRESS_MAINNET, HYPERCORE_CHAIN_ID
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultHistoricalRead, verify_parquet_file
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE

logger = logging.getLogger(__name__)


LEADER_FRACTION_REASON = "Leader share of the vault capital near allowed Hyperliquid minimum and new capital may not be accepted"


def _env_bool(name: str, *, default: bool) -> bool:
    """Read a boolean environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _get_default_parquet_paths() -> list[Path]:
    """Return default parquet paths that exist on disc."""
    return [
        path
        for path in (
            DEFAULT_UNCLEANED_PRICE_DATABASE,
            DEFAULT_RAW_PRICE_DATABASE,
        )
        if path.exists()
    ]


def _get_parquet_paths() -> list[Path]:
    """Read target parquet paths from environment or defaults."""
    paths_str = os.environ.get("PARQUET_PATHS", "").strip()
    if paths_str:
        return [Path(part.strip()).expanduser() for part in paths_str.split(",") if part.strip()]
    return _get_default_parquet_paths()


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write a parquet file using the correct writer for raw or cleaned data."""
    if path.name == DEFAULT_UNCLEANED_PRICE_DATABASE.name:
        VaultHistoricalRead.write_uncleaned_parquet(df, path)
        return

    temp_fd, temp_path = tempfile.mkstemp(
        suffix=".parquet",
        dir=str(path.parent),
    )
    try:
        os.close(temp_fd)
        df.to_parquet(temp_path, compression="zstd")
        verify_parquet_file(
            temp_path,
            expected_rows=len(df),
            required_columns=["deposits_open", "deposit_closed_reason"],
        )
        os.replace(temp_path, str(path))
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def fix_hlp_deposit_open_marker(
    path: Path,
    vault_address: str,
    *,
    dry_run: bool,
    backup: bool,
) -> dict[str, object]:
    """Fix HLP deposit-open markers in one parquet file.

    :param path:
        Parquet file to repair.
    :param vault_address:
        Lowercased vault address to repair.
    :param dry_run:
        Whether to only report candidate rows.
    :param backup:
        Whether to create a timestamped backup before writing.
    :return:
        Summary dictionary for tabular output.
    """
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "hlp_rows": 0,
            "changed_rows": 0,
            "backup": "",
        }

    df = pd.read_parquet(path)

    required = {"chain", "address", "deposits_open", "deposit_closed_reason"}
    missing = required - set(df.columns)
    if missing:
        message = f"{path} missing required columns: {sorted(missing)}"
        raise RuntimeError(message)

    address_series = df["address"].astype(str).str.lower()
    hlp_mask = (df["chain"].astype("int64") == HYPERCORE_CHAIN_ID) & (address_series == vault_address)
    reason_series = df["deposit_closed_reason"].astype(object).fillna("").astype(str)
    deposits_series = df["deposits_open"].astype(object).fillna("").astype(str)

    stale_reason_mask = reason_series == LEADER_FRACTION_REASON
    closed_mask = deposits_series == "false"
    fix_mask = hlp_mask & (stale_reason_mask | closed_mask)

    changed_rows = int(fix_mask.sum())
    backup_path = ""

    if changed_rows and not dry_run:
        if backup:
            timestamp = native_datetime_utc_now().strftime("%Y%m%d-%H%M%S")
            backup_file = path.with_name(f"{path.name}.bak-{timestamp}")
            shutil.copy2(path, backup_file)
            backup_path = str(backup_file)

        df["deposits_open"] = df["deposits_open"].astype(object)
        df["deposit_closed_reason"] = df["deposit_closed_reason"].astype(object)
        df.loc[hlp_mask, "deposits_open"] = "true"
        df.loc[hlp_mask, "deposit_closed_reason"] = ""

        _write_parquet(df, path)

    return {
        "path": str(path),
        "exists": True,
        "hlp_rows": int(hlp_mask.sum()),
        "changed_rows": changed_rows,
        "backup": backup_path,
    }


def main() -> None:
    """Run the manual HLP marker repair."""
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/fix-hlp-deposit-open-marker.log"),
    )

    paths = _get_parquet_paths()
    if not paths:
        message = "No parquet files found. Set PARQUET_PATHS to repair explicit files."
        raise RuntimeError(message)

    vault_address = os.environ.get("VAULT_ADDRESS", str(HLP_VAULT_ADDRESS_MAINNET)).strip().lower()
    dry_run = _env_bool("DRY_RUN", default=False)
    backup = _env_bool("BACKUP", default=True)

    logger.info("Repairing HLP deposit markers for %s", vault_address)
    logger.info("Dry run: %s", dry_run)

    summaries = [
        fix_hlp_deposit_open_marker(
            path=path,
            vault_address=vault_address,
            dry_run=dry_run,
            backup=backup,
        )
        for path in paths
    ]

    print(
        tabulate(
            summaries,
            headers={
                "path": "Path",
                "exists": "Exists",
                "hlp_rows": "HLP rows",
                "changed_rows": "Changed rows",
                "backup": "Backup",
            },
            tablefmt="simple",
        )
    )

    if dry_run:
        print("DRY_RUN=true - no changes made")


if __name__ == "__main__":
    main()

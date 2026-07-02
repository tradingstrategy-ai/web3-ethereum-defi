"""List blacklisted vaults from lifetime metrics.

Recalculates lifetime metrics from the local pipeline inputs, then prints all
vaults whose final metrics risk is
:py:data:`eth_defi.vault.risk.VaultTechnicalRisk.blacklisted`.

This script intentionally reads the cleaned parquet and vault metadata database
instead of ``top_vaults_by_chain.json``. The public JSON export suppresses
blacklisted rows, so it cannot be used to audit what was removed.

Usage:

.. code-block:: shell

    poetry run python scripts/erc-4626/list-blacklisted-vaults.py

Environment variables:

- ``PIPELINE_DATA_DIR``: Optional. Base directory for default pipeline files.
- ``VAULT_DB_PATH``: Optional. Vault metadata pickle path.
- ``PARQUET_FILE``: Optional. Cleaned vault prices parquet path.
- ``STABLECOINS_DIR``: Optional. Stablecoin metadata YAML directory.
- ``STABLECOINS_ONLY``: Optional. Keep the top-vault export filter. Default: true.
- ``LOG_LEVEL``: Optional. Default: warning.
"""

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
from tabulate import tabulate

from eth_defi.feed.stablecoin_rate import STABLECOINS_DATA_DIR, StablecoinRateFeeder
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics
from eth_defi.token import is_stablecoin_like
from eth_defi.utils import setup_console_logging
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    """Parse a boolean environment flag.

    :param name:
        Environment variable name.
    :param default:
        Fallback value when the variable is unset.
    :return:
        Parsed boolean.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _format_usd(value: float | int | None) -> str:
    """Format a USD amount for tabular output.

    :param value:
        USD value, or ``None`` for missing data.
    :return:
        Formatted string.
    """
    if value is None or pd.isna(value):
        return "-"
    return f"${float(value):,.0f}"


def _format_flags(value: Any) -> str:
    """Format vault flags for tabular output.

    :param value:
        Iterable of flag enums or strings.
    :return:
        Comma-separated flag values.
    """
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return ", ".join(sorted(getattr(flag, "value", str(flag)) for flag in value))


def _resolve_paths() -> tuple[Path, Path, Path]:
    """Resolve input paths from environment variables.

    :return:
        Tuple ``(vault_db_path, parquet_path, stablecoins_dir)``.
    """
    pipeline_data_dir = get_pipeline_data_dir()
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(pipeline_data_dir / "vault-metadata-db.pickle"))).expanduser()
    parquet_path = Path(os.environ.get("PARQUET_FILE", str(pipeline_data_dir / "cleaned-vault-prices-1h.parquet"))).expanduser()
    stablecoins_dir = Path(os.environ.get("STABLECOINS_DIR", str(STABLECOINS_DATA_DIR))).expanduser()
    return vault_db_path, parquet_path, stablecoins_dir


def main() -> None:
    """Calculate and print blacklisted vault rows."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))

    vault_db_path, parquet_path, stablecoins_dir = _resolve_paths()
    stablecoins_only = _env_flag("STABLECOINS_ONLY", default=True)

    assert vault_db_path.exists(), f"Vault database not found: {vault_db_path}"
    assert parquet_path.exists(), f"Cleaned vault prices parquet not found: {parquet_path}"
    assert stablecoins_dir.exists(), f"Stablecoin metadata directory not found: {stablecoins_dir}"

    logger.info("Reading vault database from %s", vault_db_path)
    vault_db = VaultDatabase.read(vault_db_path)

    logger.info("Reading cleaned vault prices from %s", parquet_path)
    prices_df = pd.read_parquet(parquet_path)

    if stablecoins_only:
        allowed_vault_ids = {f"{row['_detection_data'].chain}-{row['_detection_data'].address}" for row in vault_db.values() if is_stablecoin_like(row["Denomination"])}
        prices_df = prices_df.loc[prices_df["id"].isin(allowed_vault_ids)]

    returns_df = calculate_hourly_returns_for_all_vaults(prices_df)
    metrics = calculate_lifetime_metrics(
        returns_df,
        vault_db,
        stablecoin_rate_feeder=StablecoinRateFeeder(data_dir=stablecoins_dir),
    )

    blacklisted = metrics.loc[metrics["risk"] == VaultTechnicalRisk.blacklisted].copy()
    if not blacklisted.empty:
        blacklisted = blacklisted.sort_values(["chain", "protocol", "current_nav"], ascending=[True, True, False])

    rows: list[list[Any]] = []
    total_tvl = 0.0
    for _, row in blacklisted.iterrows():
        tvl = row.get("current_nav")
        if tvl is not None and pd.notna(tvl):
            total_tvl += float(tvl)
        rows.append(
            [
                row.get("chain"),
                row.get("protocol"),
                row.get("name"),
                row.get("denomination"),
                _format_flags(row.get("flags")),
                _format_usd(tvl),
                row.get("address"),
                row.get("notes") or "",
            ]
        )

    rows.append(["TOTAL", "", f"{len(blacklisted):,} vaults", "", "", _format_usd(total_tvl), "", ""])

    print()
    print(f"Vault database: {vault_db_path}")
    print(f"Cleaned prices: {parquet_path}")
    print(f"Stablecoin metadata: {stablecoins_dir}")
    print(f"Stablecoin-denominated only: {stablecoins_only}")
    print()
    print(
        tabulate(
            rows,
            headers=["Chain", "Protocol", "Vault", "Denomination", "Flags", "TVL", "Address", "Notes"],
            tablefmt="grid",
            colalign=("left", "left", "left", "left", "left", "right", "left", "left"),
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Print the latest collected Enzyme vault metrics from the local pipeline.

The script is read-only. It uses the latest Enzyme metadata database records,
whose onchain scan stores total value and outstanding shares. Values are
reported in each vault's accounting unit, not necessarily in USD.

Usage::

    poetry run python scripts/enzyme/export-vaults.py

Environment variables:

- ``VAULT_DB_PATH``: metadata database path.
- ``VALUE_UNITS``: optional comma-separated accounting-unit filter, such as
  ``USDC,DAI,USDT``. Use this before comparing total values: Enzyme's NAV is
  not normalised to USD by the local database.
- ``SORT_BY_TOTAL_VALUE``: set to ``true`` to order the selected rows by
  descending reported total value.
- ``LIMIT``: optional positive maximum number of rows to output.
- ``LOG_LEVEL``: optional console log level, default ``warning``.
"""

import logging
import math
import os
from decimal import Decimal
from pathlib import Path

from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


def parse_path_env(name: str, default: Path) -> Path:
    """Resolve a pipeline path from an environment variable.

    :param name: Environment variable name.
    :param default: Default pipeline path.
    :return: Selected expanded path.
    """

    return Path(os.environ.get(name, str(default))).expanduser()


def parse_bool_env(name: str) -> bool:
    """Read a conventional boolean environment variable.

    This keeps the report reproducible from a shell without adding command
    line arguments to an operational script.

    :param name: Environment variable name.
    :return: Parsed boolean value.
    :raises ValueError: If a configured value is not a recognised boolean.
    """

    value = os.environ.get(name)
    if value is None:
        return False

    normalised_value = value.strip().lower()
    if normalised_value in {"1", "true", "yes", "on"}:
        return True
    if normalised_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {value!r}")


def parse_limit_env(name: str = "LIMIT") -> int | None:
    """Read an optional positive row limit from the environment.

    :param name: Environment variable name.
    :return: Row limit, or ``None`` when no limit was configured.
    :raises ValueError: If the configured limit is not a positive integer.
    """

    value = os.environ.get(name)
    if value is None:
        return None

    limit = int(value)
    if limit < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return limit


def parse_value_units_env(name: str = "VALUE_UNITS") -> frozenset[str] | None:
    """Read an optional case-insensitive accounting-unit filter.

    :param name: Environment variable containing comma-separated asset symbols.
    :return: Uppercase selected symbols, or ``None`` when filtering is disabled.
    :raises ValueError: If the variable is set but contains no symbols.
    """

    value = os.environ.get(name)
    if value is None:
        return None

    units = frozenset(unit.strip().upper() for unit in value.split(",") if unit.strip())
    if not units:
        raise ValueError(f"{name} must contain at least one accounting unit")
    return units


def format_metric(value: object) -> str:
    """Render collected metrics without exposing Parquet nulls as ``nan``.

    :param value: Scalar value from a metadata or historical-price row.
    :return: Compact human-readable value, or an em dash when not reported.
    """

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    if isinstance(value, float | Decimal):
        return f"{value:,.6g}"
    return str(value)


def get_enzyme_protocol_version(row: VaultRow) -> str:
    """Classify an Enzyme database row as the Blue or Onyx protocol.

    The scanner's feature set is authoritative.  Old pickles can lack its
    top-level copy, so the stored detection data remains a compatibility
    fallback.

    :param row:
        Vault metadata row from :class:`~eth_defi.vault.vaultdb.VaultDatabase`.
    :return:
        ``Blue``, ``Onyx``, or ``Unknown`` when neither Enzyme feature is
        present.
    """

    detection = row.get("_detection_data")
    features = row.get("features") or getattr(detection, "features", set())
    feature_values = {feature.value if isinstance(feature, ERC4626Feature) else str(feature) for feature in features}
    if ERC4626Feature.enzyme_blue_like.value in feature_values:
        return "Blue"
    if ERC4626Feature.enzyme_onyx_like.value in feature_values or "enzyme_like" in feature_values:
        return "Onyx"
    return "Unknown"


def main() -> None:
    """Render collected Enzyme vault rows as a Markdown table.

    The report reads existing metadata only and does not contact RPC providers
    or update scanner state. It includes both current Enzyme feature families,
    including each vault's stored short description.  It preserves accounting
    units instead of incorrectly labelling all NAV values as USD; use
    ``VALUE_UNITS`` when comparing the reported total values.

    :return: None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    vault_db = VaultDatabase.read(vault_db_path)
    rows = {spec: row for spec, row in vault_db.rows.items() if row.get("Protocol") == "Enzyme"}
    value_units = parse_value_units_env()
    sort_by_total_value = parse_bool_env("SORT_BY_TOTAL_VALUE")
    limit = parse_limit_env()
    if value_units:
        rows = {spec: row for spec, row in rows.items() if (row.get("Denomination") or "").upper() in value_units}
    logger.info("Exporting %d Enzyme vault rows from %s", len(rows), vault_db_path)
    table = []
    if sort_by_total_value:
        sorted_rows = sorted(
            rows.items(),
            key=lambda item: (item[1].get("NAV") is not None, item[1].get("NAV") or Decimal(0)),
            reverse=True,
        )
    else:
        sorted_rows = sorted(rows.items(), key=lambda item: (item[0].chain_id, item[1].get("Name") or ""))

    for spec, row in sorted_rows[:limit]:
        tvl = row.get("NAV")
        shares = row.get("Shares")
        share_price = tvl / shares if tvl is not None and shares not in {None, 0} else None
        value_unit = row.get("Denomination") or "—"
        table.append(
            {
                "Chain": get_chain_name(spec.chain_id),
                "Version": get_enzyme_protocol_version(row),
                "Name": row.get("Name"),
                "Short description": row.get("_short_description") or "—",
                "Value unit": value_unit,
                "Total value (accounting unit)": format_metric(tvl),
                "Share price": format_metric(share_price),
                "Performance fee": format_metric(row.get("Perf fee")),
                "Management fee (user-facing)": format_metric(row.get("Mgmt fee")),
                # Reference only: subtract this from management only to show a
                # manager-only rate. It is already included in investor fees.
                "Protocol fee (included in management)": format_metric(row.get("Protocol fee")),
            }
        )
    print(tabulate(table, headers="keys", tablefmt="github"))


if __name__ == "__main__":
    main()

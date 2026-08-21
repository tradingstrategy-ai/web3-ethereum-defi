#!/usr/bin/env python3
"""Print the latest collected Enzyme vault metrics from the local pipeline.

The script is read-only. It uses the latest Enzyme metadata database records,
whose onchain scan stores total value and outstanding shares. Values are
reported in each vault's accounting unit, not necessarily in USD.

Usage::

    poetry run python scripts/enzyme/export-vaults.py

Environment variables:

- ``VAULT_DB_PATH``: metadata database path.
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
    preserving their accounting units instead of labelling all values as USD.

    :return: None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    vault_db_path = parse_path_env("VAULT_DB_PATH", DEFAULT_VAULT_DATABASE)
    vault_db = VaultDatabase.read(vault_db_path)
    rows = {spec: row for spec, row in vault_db.rows.items() if row.get("Protocol") == "Enzyme"}
    logger.info("Exporting %d Enzyme vault rows from %s", len(rows), vault_db_path)
    table = []
    for spec, row in sorted(rows.items(), key=lambda item: (item[0].chain_id, item[1].get("Name") or "")):
        tvl = row.get("NAV")
        shares = row.get("Shares")
        share_price = tvl / shares if tvl is not None and shares not in {None, 0} else None
        value_unit = row.get("Denomination") or (row.get("_vault_info") or {}).get("value_asset") or "—"
        table.append(
            {
                "Chain": get_chain_name(spec.chain_id),
                "Version": get_enzyme_protocol_version(row),
                "Name": row.get("Name"),
                "Short description": row.get("_short_description") or "—",
                "Value unit": value_unit,
                "Total value": format_metric(tvl),
                "Share price": format_metric(share_price),
                "Performance fee": format_metric(row.get("Perf fee")),
                "Management fee (user-facing)": format_metric(row.get("Mgmt fee")),
                "Protocol fee": format_metric(row.get("Protocol fee")),
            }
        )
    print(tabulate(table, headers="keys", tablefmt="github"))


if __name__ == "__main__":
    main()

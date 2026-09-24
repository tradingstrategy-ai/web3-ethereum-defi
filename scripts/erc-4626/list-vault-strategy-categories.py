#!/usr/bin/env python3

"""Print a local vault strategy-category breakdown sorted by USD TVL.

The script reads a locally exported vault JSON file and never uploads data. It
requires the top-level ``categories`` mapping produced by the vault exporter,
so it never presents an incomplete legacy export as a zero-valued breakdown.

Usage:

.. code-block:: shell

    INPUT_JSON=/tmp/top-vaults-by-category.json poetry run python scripts/erc-4626/list-vault-strategy-categories.py

Environment variables:

- ``INPUT_JSON``: Local vault metrics JSON input. Defaults to
  ``~/.tradingstrategy/vaults/top_vaults_by_chain.json``.
"""

import json
import logging
import math
import os
from collections.abc import Mapping
from pathlib import Path

from tabulate import tabulate

from eth_defi.research.vault_metrics import StrategyCategoryExportRecord
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


def format_usd(value: float) -> str:
    """Format one USD TVL value for the terminal table.

    :param value:
        Finite USD TVL value.
    :return:
        Whole-dollar formatted USD amount.
    """
    return f"${value:,.0f}"


def format_percent(value: float | None) -> str:
    """Format one annualised 1M APY value for the terminal table.

    :param value:
        Annualised decimal return, or ``None`` when unavailable.
    :return:
        Percentage text or a missing-value marker.
    """
    if value is None:
        return "-"
    return f"{value:.2%}"


def resolve_input_path() -> Path:
    """Resolve the local vault JSON input path from the environment.

    :return:
        Existing or expected local JSON path.
    """
    default_path = get_pipeline_data_dir() / "top_vaults_by_chain.json"
    return Path(os.environ.get("INPUT_JSON", str(default_path))).expanduser()


def create_table_rows(categories: Mapping[str, StrategyCategoryExportRecord]) -> list[dict[str, str | int]]:
    """Create sorted terminal table rows from category aggregates.

    :param categories:
        Strategy-category records keyed by tag.
    :return:
        Rows ordered by descending USD TVL and then tag identifier.
    """
    sorted_categories = sorted(
        categories.items(),
        key=lambda item: (-float(item[1]["tvl_usd"]), item[0]),
    )
    return [
        {
            "Tag": tag,
            "Label": category["label"],
            "Vaults": category["vault_count"],
            "TVL (USD)": format_usd(float(category["tvl_usd"])),
            "1M annualised return": format_percent(category["one_month_apy"]),
        }
        for tag, category in sorted_categories
    ]


def main() -> None:
    """Read the local JSON export and print its strategy-category breakdown."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    input_path = resolve_input_path()
    assert input_path.exists(), f"Vault JSON input not found: {input_path}"

    logger.info("Reading local vault JSON from %s", input_path)
    with input_path.open(encoding="utf-8") as input_file:
        export_data = json.load(input_file)

    categories = export_data.get("categories")
    assert isinstance(categories, dict), "Vault JSON has no categories mapping: category aggregation either failed or this export predates category support"
    assert all(math.isfinite(float(category["tvl_usd"])) for category in categories.values())
    rows = create_table_rows(categories)
    print(tabulate(rows, headers="keys", tablefmt="rounded_outline"))


if __name__ == "__main__":
    main()

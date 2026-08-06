#!/usr/bin/env python3
"""List all NestVault deposit and redemption routes from Nest's public API.

The output contains the product name, EVM chain, deposit token and the
chain-specific NestVault entrypoint address. Nest's public catalogue is the
contract-address authority; CMS information is joined by the shared metadata
reader but is not displayed here.

Usage:

.. code-block:: shell

    poetry run python scripts/nest/list-vaults.py
"""

import datetime
import logging
import os

from tabulate import tabulate

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import NEST_CHAIN_NAMES, fetch_nest_vaults
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def get_nest_chain_name(chain_id: int) -> str:
    """Resolve a human-friendly chain name for a Nest catalogue route.

    Nest supports Worldchain and Plume before they are present in this
    repository's generic chain-name map.

    :param chain_id:
        EVM chain identifier returned by the Nest API.

    :return:
        Human-readable chain name.
    """
    nest_name = NEST_CHAIN_NAMES.get(chain_id)
    if nest_name in {"worldchain", "plume"}:
        return nest_name.capitalize()
    return get_chain_name(chain_id)


def format_tvl(tvl_usd: float | None) -> str:
    """Format Nest's reported USD TVL for a compact terminal table.

    :param tvl_usd:
        USD TVL value from the public Nest API.

    :return:
        Dollar-formatted value or ``-`` when the API did not report TVL.
    """
    return f"${tvl_usd:,.2f}" if tvl_usd is not None else "-"


def fetch_nest_vault_rows() -> list[dict[str, str]]:
    """Fetch first-party Nest routes and convert them to presentation rows.

    :return:
        Rows sorted by product, chain and deposit-token symbol.
    """
    vaults = fetch_nest_vaults(max_cache_duration=datetime.timedelta(0))
    rows = [
        {
            "Name": vault["name"],
            "Chain": get_nest_chain_name(vault["chain_id"]),
            "Deposit token": vault["asset_symbol"],
            "TVL": format_tvl(vault["tvl_usd"]),
            "Address": vault["vault_address"],
        }
        for vault in vaults.values()
    ]
    return sorted(rows, key=lambda row: (row["Name"], row["Chain"], row["Deposit token"], row["Address"]))


def main() -> None:
    """Fetch and print the current NestVault route catalogue."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    rows = fetch_nest_vault_rows()
    if not rows:
        logger.warning("Nest's public API returned no vault routes")
        return
    print(tabulate(rows, headers="keys", tablefmt="github"))
    logger.warning("Listed %d NestVault routes", len(rows))


if __name__ == "__main__":
    main()

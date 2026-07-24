"""List indexable Symbiotic vaults from the protocol's public application API.

The default output contains Symbiotic Core V2 vaults, which are the vaults
supported by this repository's Symbiotic adapter. Set
``SYMBIOTIC_VAULT_TYPE=all`` to include every implementation type returned by
the application API.

Usage:

.. code-block:: shell

    poetry run python scripts/erc-4626/list-symbiotic-vaults.py

Environment variables:

- ``SYMBIOTIC_VAULT_TYPE``: vault implementation category to list (default
  ``v2``; set to ``all`` for all API records)
- ``SYMBIOTIC_API_BASE_URL``: public application API base URL
- ``SYMBIOTIC_CHAIN_NAME``: human-readable chain name (default ``Ethereum``)
"""

import logging
import os
from decimal import Decimal

import requests
import tabulate

from eth_defi.erc_4626.vault_protocol.symbiotic.offchain_metadata import (
    DEFAULT_APP_API_BASE_URL,
    DEFAULT_APP_CHAIN_NAME,
    SymbioticOffchainVault,
    fetch_symbiotic_offchain_vaults,
)
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def _format_tvl(tvl: Decimal | None) -> str:
    """Format the API's USD total value locked for a human-readable table.

    :param tvl:
        Current USD TVL supplied by Symbiotic, if it could be parsed.
    :return:
        Currency-formatted TVL, or a placeholder for unavailable data.
    """
    return f"${tvl:,.2f}" if tvl is not None else "-"


def _create_rows(vaults: list[SymbioticOffchainVault]) -> list[dict[str, str]]:
    """Create TVL-sorted table rows from normalised Symbiotic vault records.

    :param vaults:
        Vault records from the Symbiotic public application API.
    :return:
        Table rows with the fields used by the terminal report.
    """
    sorted_vaults = sorted(vaults, key=lambda vault: vault["tvl"] or Decimal(0), reverse=True)
    return [
        {
            "Vault name": vault["name"] or "-",
            "Chain name": vault["chain_name"],
            "Curator name": vault["curator_name"] or "-",
            "TVL": _format_tvl(vault["tvl"]),
            "Address": vault["address"],
        }
        for vault in sorted_vaults
    ]


def main() -> None:
    """Fetch and render Symbiotic vault records from the configured public API.

    The report is deliberately read-only: its TVL, vault name, and curator
    name are all values supplied by Symbiotic's offchain application API.

    :return:
        ``None`` after printing the tabulated report.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    vault_type = os.environ.get("SYMBIOTIC_VAULT_TYPE", "v2")
    vault_type = None if vault_type.lower() == "all" else vault_type
    api_base_url = os.environ.get("SYMBIOTIC_API_BASE_URL", DEFAULT_APP_API_BASE_URL)
    chain_name = os.environ.get("SYMBIOTIC_CHAIN_NAME", DEFAULT_APP_CHAIN_NAME)

    try:
        vaults = list(fetch_symbiotic_offchain_vaults(api_base_url=api_base_url, chain_name=chain_name, vault_type=vault_type))
    except (requests.RequestException, ValueError) as error:
        logger.error("Could not retrieve Symbiotic offchain vault data: %s", error)
        raise SystemExit(1) from error

    print(tabulate.tabulate(_create_rows(vaults), headers="keys", tablefmt="rounded_outline"))
    print(f"\n{len(vaults)} Symbiotic {'all-type' if vault_type is None else vault_type} vault(s) shown.")


if __name__ == "__main__":
    main()

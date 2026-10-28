"""Print the positions, collateral and redeemable liquidity of one vault.

Read-only probe used by the vault report investability check and by humans.
Supports Morpho V1 and V2, Euler Earn and EVK, and 40acres vaults, see
:py:mod:`eth_defi.vault_report.vault_probes`.

Example:

.. code-block:: shell

    source .local-test.env && \\
    VAULT_ID=8453-0xf80c0529bd94c773844e459853cd91b9263dd525 PROTOCOL_SLUG=morpho \\
    poetry run python scripts/erc-4626/probe-vault-positions.py

Environment variables:

- ``VAULT_ID``: ``{chain_id}-{address}``
- ``PROTOCOL_SLUG``: ``morpho``, ``euler`` or ``40acres``
- ``FEATURES``: optional comma-separated ERC-4626 features, e.g. ``morpho_v2_like`` or ``euler_earn_like``
- ``JSON_RPC_{CHAIN}``: RPC URL of the vault's chain
- ``LOG_LEVEL``: default ``warning``
"""

import json
import os

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault_report.vault_probes import facts_to_json, fetch_vault_facts, raise_signals


def main() -> None:
    """Probe one vault and print its facts."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    vault_id = os.environ["VAULT_ID"]
    features = [feature for feature in os.environ.get("FEATURES", "").split(",") if feature]
    facts = fetch_vault_facts(vault_id, os.environ["PROTOCOL_SLUG"], features, {})
    facts.signals = raise_signals(facts)
    data = facts_to_json({vault_id: facts})[vault_id]
    rows = [[exposure["kind"], exposure["collateral_symbol"], f"{exposure['share_of_assets']:.1%}", exposure["utilisation"], exposure["redeemable"], exposure["collateral_dex_liquidity_usd"], exposure["oracle"]] for exposure in sorted(data["exposures"], key=lambda e: -e["share_of_assets"])]
    print(tabulate(rows, headers=["Kind", "Collateral", "Share", "Utilisation", "Redeemable", "DEX liquidity USD", "Oracle"], tablefmt="fancy_grid"))
    print(json.dumps({key: value for key, value in data.items() if key != "exposures"}, indent=2, default=str))


if __name__ == "__main__":
    main()

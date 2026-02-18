"""Check Derive exchange account balance for a Lagoon vault deployment.

Reads the Derive exchange account collateral balances and account summary
using the Derive API with session key authentication.

Architecture
------------

A Lagoon vault on Derive chain uses the following address hierarchy::

    Lagoon vault contract (on-chain, ERC-4626)
        |
        +-- Safe multisig (asset holder, owns vault funds)
                |
                +-- Derive exchange account (off-chain, registered with Safe address)
                |       |
                |       +-- Subaccount(s) (hold collateral: USDC, WETH, etc.)
                |
                +-- Asset manager EOA (Safe owner, can sign transactions)

- The **Lagoon vault** (``DERIVE_INTEGRATION_TEST_VAULT_ADDRESS``) is the on-chain
  ERC-4626 contract that depositors interact with.
- The **Safe** (``DERIVE_INTEGRATION_TEST_SAFE_ADDRESS``) is the multisig that
  holds vault assets. It is also the **Derive wallet address** -- the Derive
  exchange account is registered directly against the Safe address, not a
  LightAccount derived from an EOA.
- The **asset manager** (``DERIVE_INTEGRATION_TEST_ASSET_MANAGER_PRIVATE_KEY``)
  is an EOA that is an owner of the Safe and can sign Derive API requests.
- The **session key** (``DERIVE_INTEGRATION_TEST_SESSION_KEY``) is a temporary
  private key registered on Derive for API authentication. Requests are signed
  with this key and sent with the Safe address in the ``X-LYRAWALLET`` header.

Authentication flow::

    Session key signs timestamp ---> X-LYRASIGNATURE header
    Safe address              ---> X-LYRAWALLET header
                                       |
                                       v
                               Derive API (api.lyra.finance)
                                       |
                                       v
                               Subaccount balances

Environment variables:

- ``DERIVE_INTEGRATION_TEST_ASSET_MANAGER_PRIVATE_KEY``: Asset manager EOA private key
- ``DERIVE_INTEGRATION_TEST_SESSION_KEY``: Session key private key for API auth
- ``DERIVE_INTEGRATION_TEST_VAULT_ADDRESS``: Lagoon vault address (optional, for display)
- ``DERIVE_INTEGRATION_TEST_SAFE_ADDRESS``: Safe address (used as Derive wallet address)
- ``LOG_LEVEL``: Logging level (default: info)

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/lagoon/derive-check-balance.py
"""

import logging
import os

from eth_account import Account
from tabulate import tabulate

from eth_defi.derive.account import fetch_account_summary, fetch_subaccount_ids
from eth_defi.derive.authentication import DeriveApiClient
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
    )

    asset_manager_key = os.environ["DERIVE_INTEGRATION_TEST_ASSET_MANAGER_PRIVATE_KEY"]
    session_key = os.environ["DERIVE_INTEGRATION_TEST_SESSION_KEY"]
    vault_address = os.environ.get("DERIVE_INTEGRATION_TEST_VAULT_ADDRESS")
    safe_address = os.environ["DERIVE_INTEGRATION_TEST_SAFE_ADDRESS"]

    owner_account = Account.from_key(asset_manager_key)
    logger.info("Asset manager EOA: %s", owner_account.address)

    # The Derive exchange account is registered with the Safe address as the wallet
    derive_wallet_address = safe_address
    logger.info("Derive wallet (Safe): %s", derive_wallet_address)

    # Create authenticated client for mainnet
    client = DeriveApiClient(
        owner_account=owner_account,
        derive_wallet_address=derive_wallet_address,
        is_testnet=False,
        session_key_private=session_key,
    )

    # Resolve subaccounts
    subaccount_ids = fetch_subaccount_ids(client)
    if not subaccount_ids:
        logger.warning("No subaccounts found for wallet %s", derive_wallet_address)
        return

    logger.info("Found %d subaccount(s): %s", len(subaccount_ids), subaccount_ids)

    # Print header
    print(f"\nDerive wallet:  {derive_wallet_address}")
    if vault_address:
        print(f"Vault address:  {vault_address}")
    if safe_address:
        print(f"Safe address:   {safe_address}")

    # Fetch and display each subaccount
    for sid in subaccount_ids:
        client.subaccount_id = sid
        summary = fetch_account_summary(client, subaccount_id=sid)

        print(f"\nSubaccount ID:  {sid}")

        if summary.collaterals:
            rows = [
                {
                    "Token": col.token,
                    "Available": f"{col.available:,.4f}",
                    "Total": f"{col.total:,.4f}",
                    "Locked": f"{col.locked:,.4f}",
                }
                for col in summary.collaterals
            ]
            print(tabulate(rows, headers="keys", tablefmt="fancy_grid"))
        else:
            print("No collaterals found.")

        print(f"\nTotal account value: ${summary.total_value_usd:,.2f} USD")
        if summary.margin_status:
            print(f"Margin status: {summary.margin_status}")
        if summary.initial_margin is not None:
            print(f"Initial margin: ${summary.initial_margin:,.2f}")
        if summary.maintenance_margin is not None:
            print(f"Maintenance margin: ${summary.maintenance_margin:,.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e

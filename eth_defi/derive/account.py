"""Derive account balance and collateral reading functions.

This module provides functions for reading account balances and collateral information
from Derive.xyz accounts.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CollateralBalance:
    """Single collateral balance in a Derive account.

    #: Token symbol (e.g., "USDC", "WETH")
    token: str

    #: Available balance (not used in positions)
    available: Decimal

    #: Total balance including locked
    total: Decimal

    #: Balance locked in positions
    locked: Decimal

    #: Token contract address on Derive Chain
    token_address: HexAddress | None = None
    """

    token: str
    available: Decimal
    total: Decimal
    locked: Decimal
    token_address: HexAddress | None = None


@dataclass(slots=True)
class AccountSummary:
    """Complete summary of a Derive account.

    #: Derive wallet address (smart contract wallet)
    account_address: HexAddress

    #: Subaccount ID
    subaccount_id: int

    #: List of collateral balances
    collaterals: list[CollateralBalance]

    #: Total account value in USD
    total_value_usd: Decimal

    #: Account margin status (e.g., "healthy", "warning")
    margin_status: str | None = None

    #: Initial margin requirement
    initial_margin: Decimal | None = None

    #: Maintenance margin requirement
    maintenance_margin: Decimal | None = None
    """

    account_address: HexAddress
    subaccount_id: int
    collaterals: list[CollateralBalance]
    total_value_usd: Decimal
    margin_status: str | None = None
    initial_margin: Decimal | None = None
    maintenance_margin: Decimal | None = None


def fetch_account_collaterals(
    client: "DeriveApiClient",  # type: ignore  # noqa: F821
    subaccount_id: int | None = None,
) -> list[CollateralBalance]:
    """Fetch collateral balances for a Derive subaccount.

    Requires authenticated client with session key (minimum scope: read_only).

    Example::

        from eth_defi.derive.authentication import DeriveApiClient
        from eth_defi.derive.account import fetch_account_collaterals

        client = DeriveApiClient(...)
        client.session_key_private = "0x..."

        collaterals = fetch_account_collaterals(client)
        for col in collaterals:
            print(f"{col.token}: {col.available} available, {col.locked} locked")

    :param client:
        Authenticated Derive API client with session key
    :param subaccount_id:
        Subaccount ID (defaults to client.subaccount_id)
    :return:
        List of collateral balances
    :raises ValueError:
        If authentication fails or account not found
    """
    if not client.session_key_private:
        raise ValueError("Session key required for authenticated requests. Call client.register_session_key() first.")

    sid = subaccount_id if subaccount_id is not None else client.subaccount_id

    logger.info("Fetching collaterals for subaccount %s", sid)

    # Make authenticated request
    response = client._make_jsonrpc_request(
        method="private/get_collaterals",
        params={"subaccount_id": sid},
        authenticated=True,
    )

    # Parse collaterals from response
    collaterals = []
    for col_data in response.get("collaterals", []):
        collaterals.append(
            CollateralBalance(
                token=col_data.get("currency", col_data.get("token", "UNKNOWN")),
                available=Decimal(str(col_data.get("available", "0"))),
                total=Decimal(str(col_data.get("total", "0"))),
                locked=Decimal(str(col_data.get("locked", "0"))),
                token_address=col_data.get("token_address"),
            )
        )

    logger.info("Found %d collateral(s)", len(collaterals))
    return collaterals


def fetch_account_summary(
    client: "DeriveApiClient",  # type: ignore  # noqa: F821
    subaccount_id: int | None = None,
) -> AccountSummary:
    """Fetch comprehensive account summary including collaterals and margin info.

    Combines multiple API calls to build a complete account picture.

    Example::

        from eth_defi.derive.authentication import DeriveApiClient
        from eth_defi.derive.account import fetch_account_summary

        client = DeriveApiClient(...)
        client.session_key_private = "0x..."

        summary = fetch_account_summary(client)
        print(f"Total value: ${summary.total_value_usd}")
        print(f"Margin status: {summary.margin_status}")
        for col in summary.collaterals:
            print(f"  {col.token}: {col.total}")

    :param client:
        Authenticated Derive API client with session key
    :param subaccount_id:
        Subaccount ID (defaults to client.subaccount_id)
    :return:
        Complete account summary
    :raises ValueError:
        If authentication fails or account not found
    """
    if not client.derive_wallet_address:
        raise ValueError("derive_wallet_address required")

    sid = subaccount_id if subaccount_id is not None else client.subaccount_id

    logger.info("Fetching account summary for subaccount %s", sid)

    # Fetch collaterals
    collaterals = fetch_account_collaterals(client, sid)

    # Fetch account info
    try:
        account_response = client._make_jsonrpc_request(
            method="private/get_account",
            params={"subaccount_id": sid},
            authenticated=True,
        )
    except ValueError as e:
        logger.warning("Failed to fetch account info: %s", e)
        account_response = {}

    # Fetch margin info
    try:
        margin_response = client._make_jsonrpc_request(
            method="private/get_margin",
            params={"subaccount_id": sid},
            authenticated=True,
        )
    except ValueError as e:
        logger.warning("Failed to fetch margin info: %s", e)
        margin_response = {}

    # Calculate total value from collaterals if not provided
    total_value_usd = Decimal(str(account_response.get("total_value", "0")))
    if total_value_usd == 0:
        # Fallback: sum collateral totals (assuming USDC value)
        for col in collaterals:
            if col.token.upper() == "USDC":
                total_value_usd += col.total

    return AccountSummary(
        account_address=client.derive_wallet_address,
        subaccount_id=sid,
        collaterals=collaterals,
        total_value_usd=total_value_usd,
        margin_status=margin_response.get("status"),
        initial_margin=Decimal(str(margin_response.get("initial_margin", "0"))) if margin_response.get("initial_margin") else None,
        maintenance_margin=Decimal(str(margin_response.get("maintenance_margin", "0"))) if margin_response.get("maintenance_margin") else None,
    )

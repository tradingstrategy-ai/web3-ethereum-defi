"""Lighter account valuation.

Calculate the net asset value of a Lighter trading account using the public
``/api/v1/account`` endpoint.

Lighter returns account equity directly as ``total_asset_value``. For cross
margin accounts this value matches ``collateral + sum(position.unrealized_pnl)``
within API rounding. This module preserves both values so downstream callers can
use the canonical API NAV while still logging a useful breakdown.

.. code-block:: python

    from eth_defi.lighter.session import create_lighter_session
    from eth_defi.lighter.valuation import fetch_lighter_total_equity

    session = create_lighter_session()
    result = fetch_lighter_total_equity(session, account_index=123456)

    nav = result.get_total()
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from eth_defi.lighter.session import LighterSession

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LighterEquity:
    """Equity breakdown for a Lighter trading account.

    See :py:func:`fetch_lighter_total_equity`.
    """

    #: Lighter account index.
    account_index: int

    #: Account collateral in USDC, before unrealised position PnL.
    collateral: Decimal

    #: Sum of ``positions[].unrealized_pnl`` in USDC.
    unrealised_pnl: Decimal

    #: Canonical account NAV reported by Lighter as ``total_asset_value``.
    total_asset_value: Decimal

    #: Free USDC balance available for new orders or withdrawals.
    available_balance: Decimal

    #: Cross-margin initial margin requirement in USDC.
    initial_margin_requirement: Decimal

    #: Cross-margin maintenance margin requirement in USDC.
    maintenance_margin_requirement: Decimal

    #: Number of open position records returned by the Lighter API.
    position_count: int

    def get_total(self) -> Decimal:
        """Return canonical Lighter account NAV.

        Lighter exposes ``total_asset_value`` directly, so this method returns
        the API value instead of recomputing it locally. Use
        :py:meth:`calculate_total_from_parts` for a collateral plus PnL sanity
        check.

        :return:
            Account net asset value in USDC.
        """
        return self.total_asset_value

    def calculate_total_from_parts(self) -> Decimal:
        """Calculate account NAV from collateral and unrealised PnL.

        :return:
            ``collateral + unrealised_pnl`` in USDC.
        """
        return self.collateral + self.unrealised_pnl


def fetch_lighter_total_equity(
    session: LighterSession,
    account_index: int,
    timeout: float = 30.0,
) -> LighterEquity:
    """Calculate the total equity of a Lighter trading account.

    Uses Lighter's public ``/api/v1/account?by=index&value={account_index}``
    endpoint. No API key is required. The returned
    :py:meth:`LighterEquity.get_total` value is the canonical
    ``total_asset_value`` reported by Lighter, denominated in USDC.

    Authoritative endpoint documentation:
    https://apidocs.lighter.xyz/reference/account

    :param session:
        Lighter HTTP session.
    :param account_index:
        Lighter account index.
    :param timeout:
        HTTP request timeout.
    :return:
        :class:`LighterEquity` with NAV, collateral, unrealised PnL,
        available balance, margin requirements and position count.
    :raises ValueError:
        If the API returns no account or malformed decimal fields.
    """
    account = fetch_lighter_account_by_index(
        session=session,
        account_index=account_index,
        timeout=timeout,
    )
    equity = parse_lighter_account_equity(account)
    logger.info(
        "Total equity for Lighter account %d: collateral=%s, unrealised_pnl=%s, total=%s, available=%s",
        equity.account_index,
        equity.collateral,
        equity.unrealised_pnl,
        equity.total_asset_value,
        equity.available_balance,
    )
    return equity


def fetch_lighter_account_by_index(
    session: LighterSession,
    account_index: int,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch a raw Lighter account dict by account index.

    :param session:
        Lighter HTTP session.
    :param account_index:
        Lighter account index.
    :param timeout:
        HTTP request timeout.
    :return:
        Raw account dict from the first ``accounts`` response item.
    :raises ValueError:
        If the response contains no accounts.
    """
    url = f"{session.api_url}/api/v1/account"
    params = {"by": "index", "value": str(account_index)}
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    accounts = data.get("accounts", [])
    if not accounts:
        raise ValueError(f"No Lighter account data returned for index {account_index}")

    account = accounts[0]
    returned_index_value = account.get("account_index", account.get("index"))
    if returned_index_value is None:
        msg = "Lighter API account response did not include account_index or index"
        raise ValueError(msg)
    returned_index = int(returned_index_value)
    if returned_index != account_index:
        raise ValueError(f"Lighter API returned account index {returned_index}, expected {account_index}")
    return account


def parse_lighter_account_equity(account: dict[str, Any]) -> LighterEquity:
    """Parse a raw Lighter account dict into :class:`LighterEquity`.

    The Lighter API uses strings for decimal values. This parser converts them
    to :class:`~decimal.Decimal` and treats missing optional balances as zero.

    :param account:
        Raw account dict from ``/api/v1/account``.
    :return:
        Parsed equity breakdown.
    :raises ValueError:
        If required account fields are missing or malformed.
    """
    account_index_value = account.get("account_index", account.get("index"))
    if account_index_value is None:
        msg = "Lighter account field account_index is missing"
        raise ValueError(msg)
    account_index = int(account_index_value)
    collateral = _parse_decimal(account.get("collateral"), "collateral", default=Decimal(0))
    available_balance = _parse_decimal(account.get("available_balance"), "available_balance", default=Decimal(0))
    initial_margin_requirement = _parse_decimal(account.get("cross_initial_margin_requirement"), "cross_initial_margin_requirement", default=Decimal(0))
    maintenance_margin_requirement = _parse_decimal(account.get("cross_maintenance_margin_requirement"), "cross_maintenance_margin_requirement", default=Decimal(0))

    positions = account.get("positions") or []
    unrealised_pnl = sum(
        (_parse_position_unrealised_pnl(position) for position in positions),
        Decimal(0),
    )

    total_asset_value = _parse_decimal(
        account.get("total_asset_value", account.get("cross_asset_value")),
        "total_asset_value",
        default=collateral + unrealised_pnl,
    )

    return LighterEquity(
        account_index=account_index,
        collateral=collateral,
        unrealised_pnl=unrealised_pnl,
        total_asset_value=total_asset_value,
        available_balance=available_balance,
        initial_margin_requirement=initial_margin_requirement,
        maintenance_margin_requirement=maintenance_margin_requirement,
        position_count=len(positions),
    )


def _parse_position_unrealised_pnl(position: dict[str, Any]) -> Decimal:
    """Parse unrealised PnL from a Lighter position record.

    The public account endpoint currently returns ``unrealized_pnl``. Support
    ``unrealizedPnl`` as a defensive fallback because other exchange APIs in the
    repository use camelCase for the same concept.

    :param position:
        Raw position dict from ``account["positions"]``.
    :return:
        Position unrealised PnL in USDC.
    """
    value = position.get("unrealized_pnl", position.get("unrealizedPnl"))
    return _parse_decimal(value, "positions[].unrealized_pnl", default=Decimal(0))


def _parse_decimal(value: Any, field_name: str, default: Decimal | None = None) -> Decimal:
    """Parse a decimal value returned by Lighter.

    :param value:
        Raw value from the Lighter API.
    :param field_name:
        Field name used in error messages.
    :param default:
        Value to use when ``value`` is ``None`` or an empty string.
    :return:
        Parsed decimal value.
    :raises ValueError:
        If no value/default is available or parsing fails.
    """
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"Lighter account field {field_name} is missing")

    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Lighter account field {field_name} is not a decimal: {value!r}") from e

    if not parsed.is_finite():
        raise ValueError(f"Lighter account field {field_name} is not a finite decimal: {value!r}")

    return parsed

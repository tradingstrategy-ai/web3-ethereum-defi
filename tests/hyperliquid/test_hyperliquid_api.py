"""Test Hyperliquid info API client.

Tests :py:mod:`eth_defi.hyperliquid.api` against the live Hyperliquid mainnet API.

Uses a known address with vault deposits as the test fixture.
"""

import datetime
import logging
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eth_defi.hyperliquid.api import (
    UserVaultEquity,
    fetch_perp_clearinghouse_state,
    fetch_user_vault_equities,
    fetch_vault_lockup_status,
)


def test_perp_account_value_log_is_debug_only(caplog) -> None:
    """Keep individual perp account values out of normal scanner logs."""
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "crossMarginSummary": {
                "accountValue": "100050.0",
                "totalNtlPos": "0",
                "totalRawUsd": "100050.0",
                "totalMarginUsed": "0",
            },
            "withdrawable": "100050.0",
            "assetPositions": [],
        },
    )
    session = SimpleNamespace(api_url="https://api.hyperliquid.xyz", post_info=lambda payload, timeout: response)

    with caplog.at_level(logging.INFO, logger="eth_defi.hyperliquid.api"):
        fetch_perp_clearinghouse_state(session, "0xc2d221a27eb58f5879d0400ccec8956a01634fab")

    assert "perp account value" not in caplog.text

    with caplog.at_level(logging.DEBUG, logger="eth_defi.hyperliquid.api"):
        fetch_perp_clearinghouse_state(session, "0xc2d221a27eb58f5879d0400ccec8956a01634fab")

    assert "User 0xc2d221a27eb58f5879d0400ccec8956a01634fab: perp account value 100050.0, 0 position(s)" in caplog.text


@pytest.fixture(scope="module")
def known_vault_depositor() -> str:
    """A mainnet address known to have Hypercore vault deposits.

    This is the HLP vault leader address which holds positions in multiple vaults.
    """
    return "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"


def test_fetch_user_vault_equities(session, known_vault_depositor):
    """Fetch vault equities for a known depositor."""
    equities = fetch_user_vault_equities(session, user=known_vault_depositor)

    assert len(equities) > 0, "Known depositor should have at least one vault position"

    for eq in equities:
        assert isinstance(eq, UserVaultEquity)
        assert eq.vault_address.startswith("0x")
        assert len(eq.vault_address) == 42
        assert isinstance(eq.equity, Decimal)
        assert eq.equity > 0
        assert isinstance(eq.locked_until, datetime.datetime)
        assert eq.locked_until > datetime.datetime(2020, 1, 1)


def test_fetch_user_vault_equities_empty(session):
    """An address with no vault deposits returns an empty list."""
    equities = fetch_user_vault_equities(
        session,
        user="0x0000000000000000000000000000000000000001",
    )
    assert equities == []


def test_user_vault_equity_lockup_properties():
    """Unit test for is_lockup_expired and lockup_remaining properties."""
    from eth_defi.compat import native_datetime_utc_now

    now = native_datetime_utc_now()

    # Expired lock-up (in the past)
    expired = UserVaultEquity(
        vault_address="0x0000000000000000000000000000000000000001",
        equity=Decimal("100.0"),
        locked_until=now - datetime.timedelta(hours=1),
    )
    assert expired.is_lockup_expired is True
    assert expired.lockup_remaining == datetime.timedelta(0)

    # Active lock-up (in the future)
    active = UserVaultEquity(
        vault_address="0x0000000000000000000000000000000000000002",
        equity=Decimal("200.0"),
        locked_until=now + datetime.timedelta(hours=12),
    )
    assert active.is_lockup_expired is False
    assert active.lockup_remaining > datetime.timedelta(0)
    assert active.lockup_remaining <= datetime.timedelta(hours=12)


def test_fetch_vault_lockup_status(session, known_vault_depositor):
    """Fetch lock-up status for a known depositor via the live API."""
    # First get all positions to find a vault address
    equities = fetch_user_vault_equities(session, user=known_vault_depositor)
    assert len(equities) > 0

    # Check lock-up status for the first vault
    vault_addr = equities[0].vault_address
    eq = fetch_vault_lockup_status(session, user=known_vault_depositor, vault_address=vault_addr)
    assert eq is not None
    assert isinstance(eq.is_lockup_expired, bool)
    assert isinstance(eq.lockup_remaining, datetime.timedelta)
    assert eq.lockup_remaining >= datetime.timedelta(0)

    # HLP leader deposits are old, lock-up should be expired
    assert eq.is_lockup_expired is True

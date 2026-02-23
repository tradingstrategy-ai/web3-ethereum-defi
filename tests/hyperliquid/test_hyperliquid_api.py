"""Test Hyperliquid info API client.

Tests :py:mod:`eth_defi.hyperliquid.api` against the live Hyperliquid mainnet API.

Uses a known address with vault deposits as the test fixture.
"""

import datetime
from decimal import Decimal

import pytest

from eth_defi.hyperliquid.api import UserVaultEquity, fetch_user_vault_equities


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

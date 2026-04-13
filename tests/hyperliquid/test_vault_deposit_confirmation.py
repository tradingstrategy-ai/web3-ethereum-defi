"""Test HyperCore vault deposit confirmation helpers.

1. Verify first-time vault deposits must still reach the expected amount within tolerance.
2. Verify tiny non-zero equity no longer falsely confirms a first deposit.
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from eth_defi.hyperliquid.api import (
    HypercoreDepositVerificationError,
    UserVaultEquity,
    wait_for_vault_deposit_confirmation,
)


USER_ADDR = "0x0000000000000000000000000000000000000001"
VAULT_ADDR = "0x0000000000000000000000000000000000000002"


def _make_equity(equity: Decimal) -> UserVaultEquity:
    """Build a UserVaultEquity test fixture.

    1. Create a deterministic vault address and lock-up timestamp.
    2. Inject the requested equity amount.
    3. Return the API-shaped object used by the confirmation helper.
    """
    return UserVaultEquity(
        vault_address=VAULT_ADDR,
        equity=equity,
        locked_until=datetime.datetime(2030, 1, 1),
    )


@patch("eth_defi.hyperliquid.api.time.sleep")
@patch("eth_defi.hyperliquid.api.fetch_user_vault_equity")
def test_first_deposit_requires_expected_amount_within_tolerance(
    mock_fetch,
    _mock_sleep,
):
    """Confirm a first deposit only when the new equity is near the expected amount.

    1. Mock the first vault equity read to match the expected first deposit within tolerance.
    2. Call ``wait_for_vault_deposit_confirmation()`` without existing equity.
    3. Verify the helper confirms the deposit using the expected-amount threshold.
    """
    session = MagicMock()
    mock_fetch.return_value = _make_equity(Decimal("99.50"))

    # 1. Mock the first vault equity read to match the expected first deposit within tolerance.
    result = wait_for_vault_deposit_confirmation(
        session,
        user=USER_ADDR,
        vault_address=VAULT_ADDR,
        expected_deposit=Decimal("100"),
        existing_equity=None,
        timeout=10.0,
        poll_interval=1.0,
        tolerance=Decimal("1"),
    )

    # 2. Call wait_for_vault_deposit_confirmation() without existing equity.
    # 3. Verify the helper confirms the deposit using the expected-amount threshold.
    assert result.equity == Decimal("99.50")


@patch("eth_defi.hyperliquid.api.time.sleep")
@patch("eth_defi.hyperliquid.api.time.time")
@patch("eth_defi.hyperliquid.api.fetch_user_vault_equity")
def test_first_deposit_rejects_tiny_non_zero_equity(
    mock_fetch,
    mock_time,
    _mock_sleep,
):
    """Reject dust equity so a broken first deposit does not look successful.

    1. Mock repeated first-deposit equity reads that stay far below the expected amount.
    2. Call ``wait_for_vault_deposit_confirmation()`` without existing equity and with a short timeout.
    3. Verify the helper raises ``HypercoreDepositVerificationError`` instead of accepting tiny non-zero equity.
    """
    session = MagicMock()
    mock_fetch.return_value = _make_equity(Decimal("0.01"))
    mock_time.side_effect = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]

    # 1. Mock repeated first-deposit equity reads that stay far below the expected amount.
    # 2. Call wait_for_vault_deposit_confirmation() without existing equity and with a short timeout.
    # 3. Verify the helper raises HypercoreDepositVerificationError instead of accepting tiny non-zero equity.
    with pytest.raises(HypercoreDepositVerificationError, match="could not be verified"):
        wait_for_vault_deposit_confirmation(
            session,
            user=USER_ADDR,
            vault_address=VAULT_ADDR,
            expected_deposit=Decimal("100"),
            existing_equity=None,
            timeout=3.0,
            poll_interval=1.0,
            tolerance=Decimal("1"),
        )


@patch("eth_defi.hyperliquid.api.time.sleep")
@patch("eth_defi.hyperliquid.api.fetch_user_vault_equity")
def test_existing_deposit_accepts_relative_shortfall(
    mock_fetch,
    _mock_sleep,
):
    """Accept a large existing-position deposit when the shortfall stays within relative tolerance.

    1. Mock an existing-position equity increase that lands slightly below the nominal expected amount.
    2. Call ``wait_for_vault_deposit_confirmation()`` with the existing equity baseline.
    3. Verify the helper accepts the deposit because the shortfall stays within the relative tolerance.
    """
    session = MagicMock()
    mock_fetch.return_value = _make_equity(Decimal("629.998483"))

    # 1. Mock an existing-position equity increase that lands slightly below the nominal expected amount.
    result = wait_for_vault_deposit_confirmation(
        session,
        user=USER_ADDR,
        vault_address=VAULT_ADDR,
        expected_deposit=Decimal("570.690753"),
        existing_equity=Decimal("59.559287"),
        timeout=10.0,
        poll_interval=1.0,
    )

    # 2. Call wait_for_vault_deposit_confirmation() with the existing equity baseline.
    # 3. Verify the helper accepts the deposit because the shortfall stays within the relative tolerance.
    assert result.equity == Decimal("629.998483")


@patch("eth_defi.hyperliquid.api.time.sleep")
@patch("eth_defi.hyperliquid.api.time.time")
@patch("eth_defi.hyperliquid.api.fetch_user_vault_equity")
def test_existing_deposit_rejects_large_relative_shortfall(
    mock_fetch,
    mock_time,
    _mock_sleep,
):
    """Reject an existing-position deposit when the shortfall exceeds the relative tolerance.

    1. Mock an existing-position equity increase that stays materially below the expected amount.
    2. Call ``wait_for_vault_deposit_confirmation()`` with the existing equity baseline and a short timeout.
    3. Verify the helper raises ``HypercoreDepositVerificationError``.
    """
    session = MagicMock()
    mock_fetch.return_value = _make_equity(Decimal("620.0"))
    mock_time.side_effect = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

    # 1. Mock an existing-position equity increase that stays materially below the expected amount.
    # 2. Call wait_for_vault_deposit_confirmation() with the existing equity baseline and a short timeout.
    # 3. Verify the helper raises HypercoreDepositVerificationError.
    with pytest.raises(HypercoreDepositVerificationError, match="could not be verified"):
        wait_for_vault_deposit_confirmation(
            session,
            user=USER_ADDR,
            vault_address=VAULT_ADDR,
            expected_deposit=Decimal("570.690753"),
            existing_equity=Decimal("59.559287"),
            timeout=3.0,
            poll_interval=1.0,
        )

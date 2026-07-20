"""Regression tests for Hyperliquid legacy vault withdrawal-time fees."""

import datetime

import pytest

from eth_defi.hyperliquid.constants import (
    HYPERLIQUID_VAULT_FEE_MODE,
    HYPERLIQUID_VAULT_PERFORMANCE_FEE,
)
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.research.vault_metrics import calculate_net_profit
from eth_defi.vault.fee import VaultFeeMode


def test_normal_hyperliquid_vault_uses_externalised_performance_fee() -> None:
    """Normal Hyperliquid vaults apply the 10% leader share to investor returns."""
    _, vault_row = create_hyperliquid_vault_row(
        vault_address="0x1111111111111111111111111111111111111111",
        name="Normal vault",
        description=None,
        tvl=1_000.0,
        create_time=datetime.datetime(2024, 1, 1),
    )

    assert HYPERLIQUID_VAULT_FEE_MODE == VaultFeeMode.externalised
    assert vault_row["_fees"].fee_mode == VaultFeeMode.externalised
    assert vault_row["_fees"].performance == HYPERLIQUID_VAULT_PERFORMANCE_FEE
    assert vault_row["_fees"].get_net_fees().performance == HYPERLIQUID_VAULT_PERFORMANCE_FEE


def test_hyperliquid_performance_fee_reduces_investor_return() -> None:
    """A 20% vault profit leaves 18% after the 10% leader profit share."""
    _, vault_row = create_hyperliquid_vault_row(
        vault_address="0x1111111111111111111111111111111111111111",
        name="Normal vault",
        description=None,
        tvl=1_000.0,
        create_time=datetime.datetime(2024, 1, 1),
    )
    net_fees = vault_row["_fees"].get_net_fees()

    net_return = calculate_net_profit(
        start=datetime.datetime(2024, 1, 1),
        end=datetime.datetime(2024, 2, 1),
        share_price_start=1.0,
        share_price_end=1.2,
        management_fee_annual=net_fees.management,
        performance_fee=net_fees.performance,
        deposit_fee=net_fees.deposit,
        withdrawal_fee=net_fees.withdraw,
    )

    assert net_return == pytest.approx(0.18)


def test_hyperliquid_performance_fee_does_not_charge_losses() -> None:
    """A negative return remains unchanged because no leader profit share is due."""
    _, vault_row = create_hyperliquid_vault_row(
        vault_address="0x1111111111111111111111111111111111111111",
        name="Normal vault",
        description=None,
        tvl=1_000.0,
        create_time=datetime.datetime(2024, 1, 1),
    )
    net_fees = vault_row["_fees"].get_net_fees()

    net_return = calculate_net_profit(
        start=datetime.datetime(2024, 1, 1),
        end=datetime.datetime(2024, 2, 1),
        share_price_start=1.0,
        share_price_end=0.8,
        management_fee_annual=net_fees.management,
        performance_fee=net_fees.performance,
        deposit_fee=net_fees.deposit,
        withdrawal_fee=net_fees.withdraw,
    )

    assert net_return == pytest.approx(-0.2)


def test_hyperliquid_protocol_vault_keeps_zero_performance_fee() -> None:
    """HLP protocol vaults remain the no-performance-fee exception."""
    _, vault_row = create_hyperliquid_vault_row(
        vault_address="0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
        name="HLP",
        description=None,
        tvl=1_000.0,
        create_time=datetime.datetime(2024, 1, 1),
        relationship_type="parent",
    )

    assert vault_row["_fees"].fee_mode == VaultFeeMode.externalised
    assert vault_row["_fees"].performance == 0.0
    assert vault_row["_fees"].get_net_fees().performance == 0.0

"""Unit tests for Accountable-native fee data."""

from decimal import Decimal

import pytest

from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableFeeData
from eth_defi.vault.fee import VaultFeeMode


def test_accountable_fee_data_current_manager_conversion() -> None:
    """Convert all current Accountable fee-manager fields to fractions."""
    fees = AccountableFeeData(
        block_identifier=123,
        vault_address="0x0000000000000000000000000000000000000001",
        strategy_address="0x0000000000000000000000000000000000000002",
        fee_manager_address="0x0000000000000000000000000000000000000003",
        treasury_address="0x0000000000000000000000000000000000000004",
        manager_fee_recipient_address="0x0000000000000000000000000000000000000005",
        basis_points=1_000_000,
        supports_management_fee=True,
        establishment_fee_raw=0,
        management_fee_raw=10_000,
        performance_fee_raw=200_000,
        manager_performance_fee_split_raw=750_000,
        protocol_performance_fee_split_raw=250_000,
        manager_management_fee_split_raw=600_000,
        protocol_management_fee_split_raw=400_000,
        prepayment_fee_raw=20_000,
        vault_minimum_deposit_raw=1,
        strategy_minimum_deposit_raw=1_000_000_000,
        minimum_deposit_raw=1_000_000_000,
        minimum_deposit=Decimal("1000"),
    )

    assert fees.establishment_fee == 0
    assert fees.management_fee == pytest.approx(0.01)
    assert fees.performance_fee == pytest.approx(0.20)
    assert fees.manager_performance_fee_split == pytest.approx(0.75)
    assert fees.protocol_performance_fee_split == pytest.approx(0.25)
    assert fees.manager_management_fee_split == pytest.approx(0.60)
    assert fees.protocol_management_fee_split == pytest.approx(0.40)
    assert fees.prepayment_fee == pytest.approx(0.02)
    assert fees.minimum_deposit == Decimal("1000")

    generic = fees.as_generic_fee_data()
    assert generic.fee_mode is VaultFeeMode.internalised_skimming
    assert generic.management == pytest.approx(0.01)
    assert generic.performance == pytest.approx(0.20)
    assert generic.deposit == 0.0
    assert generic.withdraw == 0.0


def test_accountable_fee_data_legacy_manager_has_known_zero_management_fee() -> None:
    """Represent the missing legacy management selector as a structural zero."""
    fees = AccountableFeeData(
        block_identifier="latest",
        vault_address="0x0000000000000000000000000000000000000001",
        strategy_address="0x0000000000000000000000000000000000000002",
        fee_manager_address="0x0000000000000000000000000000000000000003",
        treasury_address="0x0000000000000000000000000000000000000004",
        manager_fee_recipient_address="0x0000000000000000000000000000000000000005",
        basis_points=1_000_000,
        supports_management_fee=False,
        establishment_fee_raw=0,
        management_fee_raw=0,
        performance_fee_raw=200_000,
        manager_performance_fee_split_raw=750_000,
        protocol_performance_fee_split_raw=250_000,
        manager_management_fee_split_raw=None,
        protocol_management_fee_split_raw=None,
        prepayment_fee_raw=20_000,
        vault_minimum_deposit_raw=None,
        strategy_minimum_deposit_raw=None,
        minimum_deposit_raw=None,
        minimum_deposit=None,
    )

    assert fees.management_fee == 0.0
    assert fees.manager_management_fee_split is None
    assert fees.protocol_management_fee_split is None
    assert fees.as_generic_fee_data().management == 0.0

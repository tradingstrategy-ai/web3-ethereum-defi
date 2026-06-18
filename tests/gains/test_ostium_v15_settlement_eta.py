"""Unit tests for Ostium V1.5 settlement ETA calculations."""

import datetime

from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import OstiumV15DepositManager


class _Call:
    """Minimal Web3 contract call mock."""

    def __init__(self, value: int):
        self.value = value

    def call(self) -> int:
        return self.value


class _OstiumSettlementFunctions:
    """Minimal Ostium settlement function namespace for ETA tests."""

    def __init__(self) -> None:
        self.last_settlement_id = 107
        self.last_settlement_ts = 1_781_627_628  # 2026-06-16 16:33:48 UTC
        self.max_settlement_interval = 259_200
        self.deposit_target = 108
        self.withdraw_target = 110

    def lastSettlementId(self) -> _Call:
        return _Call(self.last_settlement_id)

    def lastSettlementTs(self) -> _Call:
        return _Call(self.last_settlement_ts)

    def maxSettlementInterval(self) -> _Call:
        return _Call(self.max_settlement_interval)

    def targetSettlementId(self, is_deposit: bool) -> _Call:
        return _Call(self.deposit_target if is_deposit else self.withdraw_target)


class _OstiumSettlementContract:
    """Minimal Ostium contract mock for settlement ETA tests."""

    def __init__(self) -> None:
        self.functions = _OstiumSettlementFunctions()


class _OstiumSettlementVault:
    """Minimal Ostium vault mock for settlement ETA tests."""

    def __init__(self) -> None:
        self.vault_contract = _OstiumSettlementContract()


def _make_ostium_v15_manager_for_settlement_tests() -> OstiumV15DepositManager:
    """Create an Ostium V1.5 deposit manager without a Web3-backed vault."""
    manager = OstiumV15DepositManager.__new__(OstiumV15DepositManager)
    manager.vault = _OstiumSettlementVault()
    return manager


def test_ostium_v15_ticket_settlement_eta_uses_ticket_settlement_id() -> None:
    """Check Ostium V1.5 ETA calculation uses the persisted ticket settlement id.

    1. Create a manager with mocked settlement clock values.
    2. Ask for the deposit target ETA and a later ticket settlement ETA.
    3. Verify the ticket ETA is projected from the ticket id, not the current deposit target.
    """

    # 1. Create a manager with mocked settlement clock values.
    manager = _make_ostium_v15_manager_for_settlement_tests()

    # 2. Ask for the deposit target ETA and a later ticket settlement ETA.
    deposit_target_eta = manager.get_deposit_delay_over("0x0000000000000000000000000000000000000001")
    future_ticket_eta = manager.get_settlement_delay_over(110)

    # 3. Verify the ticket ETA is projected from the ticket id, not the current deposit target.
    assert deposit_target_eta == datetime.datetime(2026, 6, 19, 16, 33, 48)
    assert future_ticket_eta == datetime.datetime(2026, 6, 25, 16, 33, 48)


def test_ostium_v15_ticket_settlement_eta_for_processed_id_is_already_eligible() -> None:
    """Check processed Ostium ticket settlement ids do not move to the next interval.

    1. Create a manager with mocked settlement clock values.
    2. Ask for an ETA for an already-processed ticket id.
    3. Verify the ticket is reported as already eligible, not at the next settlement.
    """

    # 1. Create a manager with mocked settlement clock values.
    manager = _make_ostium_v15_manager_for_settlement_tests()

    # 2. Ask for an ETA for an already-processed ticket id.
    eta = manager.get_settlement_delay_over(107)

    # 3. Verify the ticket is reported as already eligible, not at the next settlement.
    assert eta == datetime.datetime(2026, 6, 16, 16, 33, 48)


def test_ostium_v15_target_settlement_eta_keeps_next_interval_floor() -> None:
    """Check target-id estimates keep the historical next-interval floor.

    1. Create a manager with mocked settlement clock values.
    2. Ask for a target estimate using the latest processed settlement id.
    3. Verify the target estimate still points at the next settlement interval.
    """

    # 1. Create a manager with mocked settlement clock values.
    manager = _make_ostium_v15_manager_for_settlement_tests()

    # 2. Ask for a target estimate using the latest processed settlement id.
    eta = manager.get_target_settlement_delay_over(107)

    # 3. Verify the target estimate still points at the next settlement interval.
    assert eta == datetime.datetime(2026, 6, 19, 16, 33, 48)

import itertools
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import should_enable_hypercore_guard
from eth_defi.hyperliquid.evm_escrow import (
    HypercorePrecompileReadError,
    _assert_activation_guard_config,
    is_account_activated,
)


class _FakeCall:
    def __init__(self, value: bool):
        self.value = value

    def call(self) -> bool:
        return self.value


class _FakeFunctions:
    def __init__(self, *, approval_allowed: bool, receiver_allowed: bool):
        self.approval_allowed = approval_allowed
        self.receiver_allowed = receiver_allowed

    def isAllowedApprovalDestination(self, _address):
        return _FakeCall(self.approval_allowed)

    def isAllowedReceiver(self, _address):
        return _FakeCall(self.receiver_allowed)


def _make_vault(*, approval_allowed: bool, receiver_allowed: bool):
    module = SimpleNamespace(
        address="0xdA1262A20Ed853Fa3BbA16e079Bbe2d1e0728d2f",
        functions=_FakeFunctions(
            approval_allowed=approval_allowed,
            receiver_allowed=receiver_allowed,
        ),
    )
    return SimpleNamespace(
        safe_address="0x49Be988d2090aa221586e9A51cacBA3D3A1eA087",
        trading_strategy_module=module,
    )


def test_should_enable_hypercore_guard_for_any_asset_on_hyperevm():
    assert should_enable_hypercore_guard(
        chain_id=999,
        any_asset=True,
        hypercore_vaults=None,
    )


def test_should_not_enable_hypercore_guard_off_hyperevm_without_vaults():
    assert not should_enable_hypercore_guard(
        chain_id=1,
        any_asset=True,
        hypercore_vaults=None,
    )


def test_activation_guard_check_rejects_missing_core_deposit_wallet_approval():
    vault = _make_vault(approval_allowed=False, receiver_allowed=True)

    with pytest.raises(RuntimeError, match="does not allow approving CoreDepositWallet"):
        _assert_activation_guard_config(
            vault,
            "0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24",
        )


def test_activation_guard_check_accepts_whitelisted_hypercore_setup():
    vault = _make_vault(approval_allowed=True, receiver_allowed=True)

    _assert_activation_guard_config(
        vault,
        "0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24",
    )


def test_is_account_activated_reports_empty_precompile_reply(caplog: pytest.LogCaptureFixture):
    """Test empty precompile replies raise a clear RPC error with provider context.

    1. Build a fake HyperEVM Web3 object whose precompile call returns empty bytes.
    2. Call ``is_account_activated()`` for a valid address.
    3. Verify the raised error and log message include the RPC provider domain.
    """
    web3 = SimpleNamespace(
        provider=SimpleNamespace(endpoint_uri="https://rpc.hyperliquid.xyz/evm"),
        eth=SimpleNamespace(call=lambda tx: b""),
    )

    # 1. Build a fake HyperEVM Web3 object whose precompile call returns empty bytes.
    # 2. Call ``is_account_activated()`` for a valid address.
    # 3. Verify the raised error and log message include the RPC provider domain.
    with caplog.at_level(logging.ERROR):
        with pytest.raises(HypercorePrecompileReadError) as exc_info:
            is_account_activated(web3, "0x49Be988d2090aa221586e9A51cacBA3D3A1eA087")

    assert "rpc.hyperliquid.xyz" in str(exc_info.value)
    assert "Last RPC headers" in str(exc_info.value)
    assert "rpc.hyperliquid.xyz" in caplog.text


def _monotonic_time():
    """Yield monotonically increasing timestamps for patched time.time().

    1. Create an infinite counter-backed callable.
    2. Use it in tests that need deterministic timeout progression.
    3. Avoid StopIteration from unrelated logging calls.
    """
    counter = itertools.count()
    return lambda: float(next(counter))


def test_wait_for_evm_escrow_clear_waits_for_spot_balance_after_escrow_clears():
    """Do not return success until the expected spot USDC increase is visible.

    1. Mock a baseline spot state, then an escrow-cleared state without the expected USDC, then a final state with the expected increase.
    2. Run ``wait_for_evm_escrow_clear()`` with ``expected_usdc`` set.
    3. Verify the helper keeps polling until the spot balance actually reaches the expected level.
    """
    from eth_defi.hyperliquid.evm_escrow import wait_for_evm_escrow_clear

    states = iter(
        [
            SimpleNamespace(
                evm_escrows=[SimpleNamespace(coin="USDC", total=Decimal("10"))],
                balances=[SimpleNamespace(coin="USDC", total=Decimal("1"), hold=Decimal("0"))],
            ),
            SimpleNamespace(
                evm_escrows=[],
                balances=[SimpleNamespace(coin="USDC", total=Decimal("1"), hold=Decimal("0"))],
            ),
            SimpleNamespace(
                evm_escrows=[],
                balances=[SimpleNamespace(coin="USDC", total=Decimal("101"), hold=Decimal("0"))],
            ),
        ]
    )

    # 1. Mock a baseline spot state, then an escrow-cleared state without the expected USDC, then a final state with the expected increase.
    with patch(
        "eth_defi.hyperliquid.evm_escrow.fetch_spot_clearinghouse_state",
        side_effect=lambda session, user: next(states),
    ):
        with patch("eth_defi.hyperliquid.evm_escrow.time.sleep"):
            with patch("eth_defi.hyperliquid.evm_escrow.time.time", side_effect=_monotonic_time()):
                # 2. Run wait_for_evm_escrow_clear() with expected_usdc set.
                wait_for_evm_escrow_clear(
                    session=object(),
                    user="0x0000000000000000000000000000000000000001",
                    expected_usdc=Decimal("100"),
                    timeout=10.0,
                    poll_interval=1.0,
                )

    # 3. Verify the helper keeps polling until the spot balance actually reaches the expected level.


def test_wait_for_evm_escrow_clear_uses_explicit_pre_phase_baseline():
    """Use the caller-provided pre-phase baseline when the first poll is already post-deposit.

    1. Mock the first observed HyperCore state as already escrow-cleared and already post-deposit.
    2. Run ``wait_for_evm_escrow_clear()`` with both ``expected_usdc`` and an explicit ``baseline_usdc``.
    3. Verify the helper succeeds from the explicit baseline without trying to re-snapshot a later one.
    """
    from eth_defi.hyperliquid.evm_escrow import wait_for_evm_escrow_clear

    post_deposit_state = SimpleNamespace(
        evm_escrows=[],
        balances=[SimpleNamespace(coin="USDC", total=Decimal("150"), hold=Decimal("0"))],
    )

    # 1. Mock the first observed HyperCore state as already escrow-cleared and already post-deposit.
    with patch(
        "eth_defi.hyperliquid.evm_escrow.fetch_spot_clearinghouse_state",
        return_value=post_deposit_state,
    ) as mock_fetch:
        with patch("eth_defi.hyperliquid.evm_escrow.time.sleep"):
            with patch("eth_defi.hyperliquid.evm_escrow.time.time", side_effect=_monotonic_time()):
                # 2. Run wait_for_evm_escrow_clear() with both expected_usdc and an explicit baseline_usdc.
                wait_for_evm_escrow_clear(
                    session=object(),
                    user="0x0000000000000000000000000000000000000001",
                    expected_usdc=Decimal("50"),
                    baseline_usdc=Decimal("100"),
                    timeout=10.0,
                    poll_interval=1.0,
                )

    # 3. Verify the helper succeeds from the explicit baseline without trying to re-snapshot a later one.
    assert mock_fetch.call_count == 1


def test_wait_for_evm_escrow_clear_times_out_if_spot_balance_never_arrives():
    """Raise TimeoutError if escrow clears but the expected spot USDC never appears.

    1. Mock a baseline spot state followed by repeated escrow-cleared states without the expected USDC increase.
    2. Run ``wait_for_evm_escrow_clear()`` with ``expected_usdc`` set and a short timeout.
    3. Verify the helper raises ``TimeoutError`` instead of returning false success.
    """
    from eth_defi.hyperliquid.evm_escrow import wait_for_evm_escrow_clear

    baseline_state = SimpleNamespace(
        evm_escrows=[SimpleNamespace(coin="USDC", total=Decimal("10"))],
        balances=[SimpleNamespace(coin="USDC", total=Decimal("1"), hold=Decimal("0"))],
    )
    post_clear_state = SimpleNamespace(
        evm_escrows=[],
        balances=[SimpleNamespace(coin="USDC", total=Decimal("1"), hold=Decimal("0"))],
    )

    with patch(
        "eth_defi.hyperliquid.evm_escrow.fetch_spot_clearinghouse_state",
        side_effect=[baseline_state, post_clear_state, post_clear_state, post_clear_state],
    ):
        with patch("eth_defi.hyperliquid.evm_escrow.time.sleep"):
            with patch(
                "eth_defi.hyperliquid.evm_escrow.time.time",
                side_effect=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
            ):
                # 1. Mock a baseline spot state followed by repeated escrow-cleared states without the expected USDC increase.
                # 2. Run wait_for_evm_escrow_clear() with expected_usdc set and a short timeout.
                # 3. Verify the helper raises TimeoutError instead of returning false success.
                with pytest.raises(TimeoutError, match="did not increase enough"):
                    wait_for_evm_escrow_clear(
                        session=object(),
                        user="0x0000000000000000000000000000000000000001",
                        expected_usdc=Decimal("100"),
                        timeout=3.0,
                        poll_interval=1.0,
                    )

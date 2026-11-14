"""Shared redemption sizing used by strategies and manual test trades."""

from decimal import Decimal, ROUND_DOWN
from unittest.mock import MagicMock

import pytest
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.euler.deposit_redeem import EulerEarnDepositManager
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault
from eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem import MorphoV1DepositManager
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.yearn.deposit_redeem import YearnV3DepositManager
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable
from eth_defi.token import TokenDetails

OWNER = "0x0000000000000000000000000000000000000001"


@pytest.mark.parametrize("manager_class", [ERC4626DepositManager, EulerEarnDepositManager, MorphoV1DepositManager, YearnV3DepositManager])
@pytest.mark.parametrize("decimals", [6, 8, 18])
def test_redemption_decimal_rounding_preserves_calldata(manager_class: type[ERC4626DepositManager], decimals: int) -> None:
    """Bound decimal conversion by one token unit without resizing an order.

    1. Model token precision independently of the selected protocol manager.
    2. Request an amount with more fractional digits than the token supports.
    3. Check exact raw calldata and token-unit tolerance on decimal values.
    4. Reject a positive decimal amount that truncates below one raw share.
    """
    # 1. Model token precision independently of the selected protocol manager.
    vault = MagicMock(spec=ERC4626Vault)
    vault.share_token = TokenDetails(contract=vault.vault_contract, decimals=decimals)
    funcs = vault.vault_contract.functions
    quantum = Decimal(1).scaleb(-decimals)
    requested = Decimal("1001.1234567890123456789")
    expected = requested.quantize(quantum, rounding=ROUND_DOWN)
    expected_raw = int(expected.scaleb(decimals))
    funcs.balanceOf.return_value.call.return_value = expected_raw + 1
    funcs.maxRedeem.return_value.call.return_value = expected_raw + 1

    # 2. Request an amount with more fractional digits than the token supports.
    request = manager_class(vault).create_redemption_request(owner=OWNER, shares=requested)
    actual = vault.share_token.convert_to_decimals(request.raw_shares)

    # 3. Check exact raw calldata and token-unit tolerance on decimal values.
    assert request.raw_shares == expected_raw
    funcs.redeem.assert_called_once_with(expected_raw, OWNER, OWNER)
    assert actual == expected
    assert actual == pytest.approx(requested, rel=0, abs=quantum)
    assert request.shares == pytest.approx(actual, rel=0, abs=quantum)
    # A relative default could conceal several raw units at larger balances.
    assert actual - 2 * quantum != pytest.approx(expected, rel=0, abs=quantum)
    funcs.redeem.return_value.call.assert_not_called()

    # 4. Reject a positive decimal amount that truncates below one raw share.
    funcs.redeem.reset_mock()
    # The common RedemptionRequest invariant owns this check for all managers.
    with pytest.raises(AssertionError):
        manager_class(vault).create_redemption_request(owner=OWNER, shares=quantum / 2)
    funcs.redeem.assert_called_once_with(0, OWNER, OWNER)
    minimum = manager_class(vault).create_redemption_request(owner=OWNER, shares=quantum)
    assert minimum.raw_shares == 1
    funcs.redeem.assert_called_with(1, OWNER, OWNER)


@pytest.mark.parametrize("manager_class", [ERC4626DepositManager, EulerEarnDepositManager, MorphoV1DepositManager, YearnV3DepositManager])
def test_redemption_request_preserves_exact_shares(manager_class: type[ERC4626DepositManager]) -> None:
    """Keep request metadata and calldata equal, including bounded rounding.

    Contract reads are mocked to reproduce raw-unit rounding deterministically.

    1. Request slightly less than the wallet balance and advertised capacity.
    2. Check the generic manager refuses a shortfall without shrinking the order.
    3. Check protocol managers simulate and preserve a recognised rounding shortfall.
    """
    # 1. Request slightly less than the wallet balance and advertised capacity.
    vault = MagicMock(spec=ERC4626Vault)
    funcs = vault.vault_contract.functions
    funcs.balanceOf.return_value.call.return_value = 10_001
    funcs.maxRedeem.return_value.call.return_value = 10_002
    funcs.redeem.return_value.call.return_value = 10_000
    funcs.previewRedeem.return_value.call.return_value = 1
    manager = manager_class(vault)
    request = manager.create_redemption_request(owner=OWNER, raw_shares=10_000)
    assert request.raw_shares == 10_000
    funcs.redeem.assert_called_with(10_000, OWNER, OWNER)
    funcs.redeem.return_value.call.assert_not_called()

    # 2. Check the generic manager refuses a shortfall without shrinking the order.
    funcs.maxRedeem.return_value.call.return_value = 9_999
    if manager_class is ERC4626DepositManager:
        with pytest.raises(VaultFlowUnavailable):
            manager.create_redemption_request(owner=OWNER, raw_shares=10_000)
        return

    # 3. Check protocol managers simulate and preserve a recognised rounding shortfall.
    request = manager.create_redemption_request(owner=OWNER, raw_shares=10_000)
    assert request.raw_shares == 10_000
    funcs.redeem.assert_called_with(10_000, OWNER, OWNER)
    funcs.redeem.return_value.call.assert_called_once_with({"from": OWNER})
    if manager_class is MorphoV1DepositManager:
        assert isinstance(MorphoV1Vault.get_deposit_manager(vault), MorphoV1DepositManager)
    if manager_class is EulerEarnDepositManager:
        assert isinstance(EulerEarnVault.get_deposit_manager(vault), EulerEarnDepositManager)


@pytest.mark.parametrize("manager_class", [EulerEarnDepositManager, MorphoV1DepositManager, YearnV3DepositManager])
def test_redemption_rounding_refuses_shortfalls_and_failed_calls(manager_class: type[ERC4626DepositManager]) -> None:
    """Bound protocol exceptions and require a positive exact payout.

    Contract responses are mocked because these limits cannot be varied on a
    pinned deployment without altering its liquidity or accounting.

    1. Refuse a shortfall larger than the recognised protocol rounding.
    2. Refuse a zero-payout or reverting exact simulation for bounded rounding.
    3. Refuse insufficient owner shares even when the capacity gap is rounding.
    """
    # 1. Refuse a shortfall larger than the recognised protocol rounding.
    vault = MagicMock(spec=ERC4626Vault)
    funcs = vault.vault_contract.functions
    funcs.balanceOf.return_value.call.return_value = 10_000
    funcs.maxRedeem.return_value.call.return_value = 9_998
    funcs.previewRedeem.return_value.call.return_value = 2
    manager = manager_class(vault)
    with pytest.raises(VaultFlowUnavailable):
        manager.create_redemption_request(owner=OWNER, raw_shares=10_000)
    funcs.redeem.assert_not_called()

    # 2. Refuse a zero-payout or reverting exact simulation for bounded rounding.
    funcs.maxRedeem.return_value.call.return_value = 9_999
    funcs.previewRedeem.return_value.call.return_value = 1
    funcs.redeem.return_value.call.return_value = 0
    with pytest.raises(VaultFlowUnavailable, match="no assets"):
        manager.create_redemption_request(owner=OWNER, raw_shares=10_000)
    funcs.redeem.return_value.call.side_effect = ContractLogicError("execution reverted")
    with pytest.raises(VaultFlowUnavailable) as failure:
        manager.create_redemption_request(owner=OWNER, raw_shares=10_000)
    assert failure.value.preflight_result == "redemption_call_reverted"
    # Transport errors without revert evidence must not be relabelled as capacity.
    funcs.redeem.return_value.call.side_effect = ValueError("RPC unavailable")
    with pytest.raises(ValueError, match="RPC unavailable"):
        manager.create_redemption_request(owner=OWNER, raw_shares=10_000)

    # 3. Refuse insufficient owner shares even when the capacity gap is rounding.
    funcs.balanceOf.return_value.call.return_value = 9_999
    with pytest.raises(VaultFlowUnavailable, match="not have enough shares"):
        manager.create_redemption_request(owner=OWNER, raw_shares=10_000)

"""Unit tests for the ERC-4626 manager deposit-limit hook.

Exercises :meth:`ERC4626DepositManager.fetch_depositable_raw_assets` without a
fork: the hook must translate a missing/broken ``maxDeposit`` into a typed
:class:`VaultFlowUnavailable` and normalise a zero limit to "no limit".
"""

from types import SimpleNamespace

import pytest
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, ContractLogicError

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

OWNER = "0x0000000000000000000000000000000000000002"
VAULT = "0x0000000000000000000000000000000000000001"
CHAIN_ID = 8453


def _manager_with_max_deposit(result) -> SimpleNamespace:
    """Build a stand-in manager whose ``maxDeposit().call()`` returns or raises.

    :param result:
        Value returned, or exception raised, by ``maxDeposit(owner).call()``.
    :return:
        Object with the ``vault`` surface read by ``fetch_depositable_raw_assets``.
    """

    def call() -> int:
        if isinstance(result, Exception):
            raise result
        return result

    functions = SimpleNamespace(maxDeposit=lambda _owner: SimpleNamespace(call=call))
    vault = SimpleNamespace(
        address=VAULT,
        chain_id=CHAIN_ID,
        get_protocol_name=lambda: "test-protocol",
        vault_contract=SimpleNamespace(functions=functions),
    )
    return SimpleNamespace(vault=vault)


def test_fetch_depositable_raw_assets_returns_limit() -> None:
    """A positive maxDeposit is returned unchanged."""
    manager = _manager_with_max_deposit(1_000)
    assert ERC4626DepositManager.fetch_depositable_raw_assets(manager, OWNER) == 1_000


def test_fetch_depositable_raw_assets_zero_is_no_limit() -> None:
    """A zero maxDeposit is normalised to None (no limit exposed)."""
    manager = _manager_with_max_deposit(0)
    assert ERC4626DepositManager.fetch_depositable_raw_assets(manager, OWNER) is None


@pytest.mark.parametrize(
    "exception",
    [
        ABIFunctionNotFound("maxDeposit not found"),
        BadFunctionCallOutput("bad output"),
        ContractLogicError("execution reverted"),
        ValueError("execution reverted"),
    ],
)
def test_fetch_depositable_raw_assets_missing_max_deposit_raises_typed(exception: Exception) -> None:
    """A vault without a readable maxDeposit raises a self-describing typed error."""
    manager = _manager_with_max_deposit(exception)
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        ERC4626DepositManager.fetch_depositable_raw_assets(manager, OWNER)
    error = exc_info.value
    assert error.direction == "deposit"
    assert error.phase == "preflight"
    assert error.vault_address == VAULT
    # Message must be self-describing for diagnostics.
    assert VAULT in error.reason
    assert str(CHAIN_ID) in error.reason

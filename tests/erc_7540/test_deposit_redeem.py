"""Unit tests for protocol-neutral ERC-7540 flow support."""

import pytest

from eth_defi.erc_4626.vault_protocol.nashpoint.vault import NashpointNodeVault
from eth_defi.erc_4626.vault_protocol.untangle.vault import UntangleVault
from eth_defi.erc_4626.vault_protocol.usdai.vault import StakedUSDaiVault
from eth_defi.erc_7540.deposit_redeem import ERC7540DepositManager, ERC7540DepositRequest, ERC7540RedemptionRequest
from eth_defi.erc_7540.vault import ERC7540Vault
from eth_defi.vault.base import VaultSpec

VAULT_ADDRESS = "0x0000000000000000000000000000000000000001"
OWNER_ADDRESS = "0x0000000000000000000000000000000000000002"


class FakeERC7540Functions:
    """Minimal standard ERC-7540 transaction builder."""

    def __init__(self) -> None:
        self.deposit_function = object()

    def requestDeposit(self, raw_amount: int, controller: str, owner: str):  # noqa: N802
        """Build a deterministic fake request."""
        assert raw_amount == 1
        assert controller == OWNER_ADDRESS
        assert owner == OWNER_ADDRESS
        return self.deposit_function


@pytest.mark.parametrize(
    "vault_class",
    [
        StakedUSDaiVault,
        UntangleVault,
        NashpointNodeVault,
    ],
)
def test_non_lagoon_protocols_use_generic_erc7540_manager(vault_class: type[ERC7540Vault]) -> None:
    """Non-Lagoon adapters must not execute Lagoon access-policy preflights."""
    functions = FakeERC7540Functions()
    vault = object.__new__(vault_class)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    vault.__dict__["vault_contract"] = type("FakeERC7540Contract", (), {"functions": functions})()

    manager = vault.get_deposit_manager()
    manager._is_vault_paused = lambda: False
    request = manager.create_deposit_request(OWNER_ADDRESS, raw_amount=1)

    assert type(manager) is ERC7540DepositManager
    assert type(request) is ERC7540DepositRequest
    assert request.funcs == [functions.deposit_function]
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "asynchronous",
        "redemption_flow": "asynchronous",
    }


def test_generic_erc7540_redemption_accepts_raw_shares() -> None:
    """Generic ERC-7540 redemption can preserve an exact raw share balance."""
    vault = object.__new__(StakedUSDaiVault)
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    redeem_function = object()
    vault.request_redeem = lambda owner, raw_shares, check_enough_token: redeem_function
    manager = vault.get_deposit_manager()

    request = manager.create_redemption_request(
        owner=OWNER_ADDRESS,
        raw_shares=123,
        check_enough_token=False,
    )

    assert type(request) is ERC7540RedemptionRequest
    assert request.raw_shares == 123
    assert request.funcs == [redeem_function]

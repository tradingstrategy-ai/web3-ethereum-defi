"""Unit tests for protocol-neutral ERC-7540 flow support."""

import pytest
from hexbytes import HexBytes

from eth_defi.erc_4626.vault_protocol.nashpoint.vault import NashpointNodeVault
from eth_defi.erc_4626.vault_protocol.untangle.vault import UntangleVault
from eth_defi.erc_4626.vault_protocol.usdai.vault import StakedUSDaiVault
from eth_defi.erc_7540.deposit_redeem import ERC7540DepositManager, ERC7540DepositRequest, ERC7540RedemptionRequest
from eth_defi.erc_7540.vault import ERC7540Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import DepositRedeemEventFailure

VAULT_ADDRESS = "0x0000000000000000000000000000000000000001"
OWNER_ADDRESS = "0x0000000000000000000000000000000000000002"
TX_HASH = HexBytes("0x" + "01" * 32)


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


class FakeFailedTransactionEth:
    """Minimal Web3 ``eth`` namespace for a reverted transaction."""

    def get_transaction_receipt(self, tx_hash: HexBytes) -> dict[str, int]:
        """Return a standard failed transaction receipt.

        :param tx_hash:
            Transaction hash requested by the analyser.
        :return:
            Receipt containing only the standard failure status.
        """
        assert tx_hash == TX_HASH
        return {"status": 0}


class FakeFailedTransactionWeb3:
    """Minimal Web3 provider exposing a failed receipt."""

    def __init__(self) -> None:
        """Initialise the fake ``eth`` namespace."""
        self.eth = FakeFailedTransactionEth()


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
        "supports_anvil_settlement": False,
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


@pytest.mark.parametrize(
    ("method_name", "direction"),
    [
        ("analyse_deposit", "deposit"),
        ("analyse_redemption", "redeem"),
    ],
)
def test_generic_erc7540_failed_claim_returns_diagnostic(
    method_name: str,
    direction: str,
) -> None:
    """A failed claim receipt must not require a non-standard revert field."""
    vault = object.__new__(StakedUSDaiVault)
    vault.web3 = FakeFailedTransactionWeb3()
    vault.spec = VaultSpec(chain_id=1, vault_address=VAULT_ADDRESS)
    manager = vault.get_deposit_manager()

    result = getattr(manager, method_name)(TX_HASH, object())

    assert isinstance(result, DepositRedeemEventFailure)
    assert result.tx_hash == TX_HASH
    assert result.revert_reason == "Transaction reverted"
    assert result.vault_address == VAULT_ADDRESS
    assert result.direction == direction
    assert result.phase == "claim"
    assert result.receipt_status == 0

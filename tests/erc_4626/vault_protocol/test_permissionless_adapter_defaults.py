"""Unit regressions for protocol-level deposit permission defaults."""

from unittest.mock import MagicMock

import pytest

from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountablePermissionLevel, AccountableVault
from eth_defi.erc_4626.vault_protocol.csigma.vault import CsigmaVault
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.erc_4626.vault_protocol.forty_acres.vault import FortyAcresVault
from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusVault
from eth_defi.erc_4626.vault_protocol.yearn.compounder import YearnCompounderVault
from eth_defi.vault.base import VaultSpec

OWNER = "0x0000000000000000000000000000000000000001"


@pytest.mark.parametrize(
    "vault_type",
    (FortyAcresVault, PlutusVault, CsigmaVault, YearnCompounderVault),
)
def test_protocol_adapters_default_to_permissionless(vault_type: type) -> None:
    """Public protocol adapters must not inherit the base unknown policy.

    1. Construct each adapter without reading unrelated live vault state.
    2. Read its protocol-level identity admission policy.
    3. Confirm the adapter reports permissionless access.
    """
    # 1. Construct each adapter without reading unrelated live vault state.
    vault = object.__new__(vault_type)

    # 2. Read its protocol-level identity admission policy.
    permissioned = vault.is_whitelisted_deposit()

    # 3. Confirm the adapter reports permissionless access.
    assert permissioned is False


def test_accountable_uses_explicit_contract_permission_level() -> None:
    """Accountable must distinguish public, KYC, and whitelist deployments.

    1. Prepare an Accountable adapter with its verified permission ABI.
    2. Check mode zero and both identity-gated modes.
    3. Confirm whitelist mode reads persistent account membership.
    4. Refuse unknown future enum values instead of guessing.
    """
    # 1. Prepare an Accountable adapter with its verified permission ABI.
    vault = object.__new__(AccountableVault)
    vault.__dict__["vault_contract"] = MagicMock()
    vault._get_block_identifier = MagicMock(return_value=123)
    permission_call = vault.vault_contract.functions.permissionLevel.return_value.call
    allowed_call = vault.vault_contract.functions.allowed.return_value.call

    # 2. Check mode zero and both identity-gated modes.
    permission_call.return_value = AccountablePermissionLevel.none
    assert vault.fetch_permission_level() is AccountablePermissionLevel.none
    assert vault.is_whitelisted_deposit() is False
    assert vault.is_account_whitelisted(OWNER) is True
    permission_call.return_value = AccountablePermissionLevel.kyc
    assert vault.is_whitelisted_deposit() is True
    assert vault.is_account_whitelisted(OWNER) is False
    assert not allowed_call.called

    # 3. Confirm whitelist mode reads persistent account membership.
    permission_call.return_value = AccountablePermissionLevel.whitelist
    allowed_call.return_value = True
    assert vault.is_whitelisted_deposit() is True
    assert vault.is_account_whitelisted(OWNER) is True
    allowed_call.assert_called_once_with(block_identifier=123)

    # 4. Refuse unknown future enum values instead of guessing.
    permission_call.return_value = 3
    with pytest.raises(NotImplementedError, match="Unknown Accountable permission level 3"):
        vault.fetch_permission_level()


def test_ember_admission_uses_deployment_validator() -> None:
    """Ember admission follows its configured validator rather than a default.

    1. Model the validator boundary without requiring an RPC connection.
    2. Check disabled and empty validators permit public deposits.
    3. Check a populated list uses the actual owner's membership.
    """
    # 1. Mock only the contract reader; exercise the adapter's real policy.
    vault = object.__new__(EmberVault)
    vault.spec = VaultSpec(1, OWNER)
    vault.__dict__["deposit_validator"] = None
    # 2. Disabled and empty validators permit public deposits.
    assert vault.is_whitelisted_deposit() is False
    assert vault.is_account_whitelisted(OWNER) is True
    validator = MagicMock()
    vault.__dict__["deposit_validator"] = validator
    validator.functions.depositAllowListCount.return_value.call.return_value = 0
    assert vault.is_whitelisted_deposit() is False
    assert vault.is_account_whitelisted(OWNER) is True
    # 3. A populated list requires the real owner's membership.
    validator.functions.depositAllowListCount.return_value.call.return_value = 2
    assert vault.is_whitelisted_deposit() is True
    allowed = validator.functions.depositAllowList.return_value.call
    allowed.return_value = True
    assert vault.is_account_whitelisted(OWNER) is True
    allowed.return_value = False
    assert vault.is_account_whitelisted(OWNER) is False
    validator.functions.depositAllowList.assert_called_with(vault.address, OWNER)

"""Unit coverage for Yearn and Upshift deposit-permission discovery."""

from unittest.mock import MagicMock

import pytest

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault


def test_yearn_without_deposit_limit_module_is_permissionless() -> None:
    """Canonical Yearn V3 limits are not account admission gates.

    1. Prepare a Yearn V3 vault with its deposit-limit module disabled.
    2. Read the vault-wide deposit permission classification.
    3. Confirm the canonical configuration is permissionless.
    """
    # 1. Prepare a Yearn V3 vault with its deposit-limit module disabled.
    vault = object.__new__(YearnV3Vault)
    vault.__dict__["vault_contract"] = MagicMock()
    vault.vault_contract.functions.deposit_limit_module.return_value.call.return_value = ZERO_ADDRESS_STR
    vault._get_block_identifier = MagicMock(return_value=123)

    # 2. Read the vault-wide deposit permission classification.
    permissioned = vault.is_whitelisted_deposit()

    # 3. Confirm the canonical configuration is permissionless.
    assert permissioned is False
    vault.vault_contract.functions.deposit_limit_module.return_value.call.assert_called_once_with(
        block_identifier=123,
    )


def test_yearn_custom_deposit_limit_module_remains_unknown() -> None:
    """A custom Yearn module must not be assumed public or allow-listed.

    1. Prepare a Yearn V3 vault with a non-zero deposit-limit module.
    2. Read the vault-wide deposit permission classification.
    3. Confirm the adapter requires module-specific inspection.
    """
    # 1. Prepare a Yearn V3 vault with a non-zero deposit-limit module.
    module = "0x1111111111111111111111111111111111111111"
    vault = object.__new__(YearnV3Vault)
    vault.__dict__["vault_contract"] = MagicMock()
    vault.vault_contract.functions.deposit_limit_module.return_value.call.return_value = module
    vault._get_block_identifier = MagicMock(return_value=123)

    # 2. Read the vault-wide deposit permission classification.
    with pytest.raises(NotImplementedError, match=module):
        vault.is_whitelisted_deposit()

    # 3. Confirm the adapter requires module-specific inspection.
    vault.vault_contract.functions.deposit_limit_module.return_value.call.assert_called_once_with(
        block_identifier=123,
    )


def test_yearn_unreadable_deposit_limit_module_remains_unknown() -> None:
    """A Yearn-like deployment without the V3 module getter must fail closed.

    1. Prepare a Yearn deployment whose deposit-limit module call reverts.
    2. Read the vault-wide deposit permission classification.
    3. Confirm the adapter reports an unknown policy instead of leaking the RPC error.
    """
    # 1. Prepare a Yearn deployment whose deposit-limit module call reverts.
    vault = object.__new__(YearnV3Vault)
    vault.__dict__["vault_contract"] = MagicMock()
    vault.vault_contract.functions.deposit_limit_module.return_value.call.side_effect = ValueError("execution reverted")
    vault._get_block_identifier = MagicMock(return_value=123)

    # 2. Read the vault-wide deposit permission classification.
    with pytest.raises(NotImplementedError, match="does not expose a readable deposit-limit module"):
        vault.is_whitelisted_deposit()

    # 3. Confirm the adapter reports an unknown policy instead of leaking the RPC error.
    vault.vault_contract.functions.deposit_limit_module.return_value.call.assert_called_once_with(
        block_identifier=123,
    )


def test_upshift_account_deposits_are_permissionless() -> None:
    """Upshift's accepted-asset list is not an account whitelist.

    1. Prepare an Upshift vault adapter without querying chain state.
    2. Read its account deposit permission classification.
    3. Confirm account admission is permissionless.
    """
    # 1. Prepare an Upshift vault adapter without querying chain state.
    vault = object.__new__(UpshiftVault)

    # 2. Read its account deposit permission classification.
    permissioned = vault.is_whitelisted_deposit()

    # 3. Confirm account admission is permissionless.
    assert permissioned is False

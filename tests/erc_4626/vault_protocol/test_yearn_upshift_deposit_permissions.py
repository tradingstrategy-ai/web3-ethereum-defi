"""Unit coverage for Yearn and Upshift deposit-permission discovery."""

from unittest.mock import MagicMock

import pytest

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.erc_4626.vault_protocol.yearn.notes import YBOLD_VAULT, YSYBOLD_VAULT
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.base import VaultSpec


def test_ybold_note_links_to_yield_bearing_staking_vault() -> None:
    """yBOLD must explain that only the ysyBOLD staking vault earns yield."""
    vault = object.__new__(YearnV3Vault)
    vault.spec = VaultSpec(chain_id=1, vault_address=YBOLD_VAULT)
    vault.features = None

    note = vault.get_notes()

    assert note is not None
    assert "does not accrue yield" in note
    assert f"[ysyBOLD](https://yearn.fi/vaults/1/{YSYBOLD_VAULT})" in note


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


def test_legacy_yearn_without_deposit_limit_module_defaults_permissionless() -> None:
    """A legacy Yearn deployment without the V3 module getter defaults public.

    1. Prepare a Yearn deployment whose deposit-limit module call reverts.
    2. Read the vault-wide deposit permission classification.
    3. Confirm the adapter reports permissionless with an explicit caveat.
    """
    # 1. Prepare a Yearn deployment whose deposit-limit module call reverts.
    vault = object.__new__(YearnV3Vault)
    vault.__dict__["vault_contract"] = MagicMock()
    vault.vault_contract.functions.deposit_limit_module.return_value.call.side_effect = ValueError("execution reverted")
    vault._get_block_identifier = MagicMock(return_value=123)

    # 2. Read the vault-wide deposit permission classification.
    permissioned = vault.is_whitelisted_deposit()

    # 3. Confirm the adapter reports permissionless with an explicit caveat.
    assert permissioned is False
    assert "permissionless compatibility default" in vault.get_whitelist_notes()
    vault.vault_contract.functions.deposit_limit_module.return_value.call.assert_called_once_with(
        block_identifier=123,
    )


def test_yearn_permission_read_does_not_hide_rpc_failures() -> None:
    """Do not misclassify a Yearn vault when its permission read fails in transit.

    1. Prepare a Yearn deposit-limit read that fails without an EVM revert.
    2. Ask the adapter to classify deposit permission.
    3. Confirm the RPC failure propagates to the scanner's unknown-status guard.
    """
    # 1. Prepare a Yearn deposit-limit read that fails without an EVM revert.
    vault = object.__new__(YearnV3Vault)
    vault.__dict__["vault_contract"] = MagicMock()
    vault._get_block_identifier = MagicMock(return_value=123)
    vault.vault_contract.functions.deposit_limit_module.return_value.call.side_effect = ValueError("RPC request timed out")

    # 2. Ask the adapter to classify deposit permission.
    with pytest.raises(ValueError, match="RPC request timed out"):
        vault.is_whitelisted_deposit()

    # 3. The transport failure propagated instead of becoming permissionless.


def test_legacy_upshift_has_no_positive_allow_list() -> None:
    """Legacy Upshift blacklists are distinct from positive allow-list membership.

    1. Prepare an Upshift vault adapter without querying chain state.
    2. Read its account deposit permission classification.
    3. Confirm account admission is permissionless.
    """
    # 1. Prepare an Upshift vault adapter without querying chain state.
    vault = object.__new__(UpshiftVault)
    vault.features = set()

    # 2. Read its account deposit permission classification.
    permissioned = vault.is_whitelisted_deposit()

    # 3. Confirm account admission is permissionless.
    assert permissioned is False


def test_upshift_sender_whitelist_is_not_an_asset_whitelist() -> None:
    """Read a configured sender policy rather than assuming public admission.

    1. Bind a multi-asset pool with a configured sender whitelist.
    2. Check that policy classification reports positive account admission.
    3. Disable that policy and verify the vault is permissionless.
    """
    # 1. Bind a multi-asset pool with a configured sender whitelist.
    vault = object.__new__(UpshiftVault)
    vault.features = {ERC4626Feature.upshift_multi_asset_like}
    vault.__dict__["vault_contract"] = MagicMock()
    call = vault.vault_contract.functions.sendersWhitelistAddress.return_value.call
    call.return_value = "0x1111111111111111111111111111111111111111"
    # 2. Check that policy classification reports positive account admission.
    assert vault.is_whitelisted_deposit() is True
    # 3. Disable that policy and verify the vault is permissionless.
    call.return_value = ZERO_ADDRESS_STR
    assert vault.is_whitelisted_deposit() is False


def test_upshift_optional_cap_requires_verified_implementation() -> None:
    """Recognise the verified cap-less version without swallowing unknown failures.

    1. Simulate an absent cap getter on the source-verified Wildcat implementation.
    2. Confirm only that implementation has a verified absent global cap.
    3. Refuse the same failure on an unknown implementation.
    """
    # 1. Simulate an absent cap getter on the source-verified Wildcat implementation.
    vault = MagicMock(spec=UpshiftVault)
    vault.web3 = MagicMock()
    vault.address = "0x18EE038C114a07f4B08b420fb1E4149a4F357249"
    vault.upshift_contract.functions.depositCap.return_value.call.side_effect = ExtraValueError("execution reverted")
    vault.web3.eth.get_storage_at.return_value = bytes.fromhex("00" * 12 + "aacc687cea8cd706bd7a56be0f7ad85d420767dc")
    # 2. Confirm only that implementation has a verified absent global cap.
    assert UpshiftVault.fetch_deposit_cap(vault) is None
    # 3. Refuse the same failure on an unknown implementation.
    vault.web3.eth.get_storage_at.return_value = bytes.fromhex("00" * 12 + "11" * 20)
    with pytest.raises(ExtraValueError, match="execution reverted"):
        UpshiftVault.fetch_deposit_cap(vault)

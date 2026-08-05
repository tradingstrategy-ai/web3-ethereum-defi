"""Unit coverage for Morpho and Euler deposit-permission discovery."""

from unittest.mock import patch

import pytest

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.euler.vault import EULER_OP_DEPOSIT, EulerEarnVault, EulerVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault


def test_canonical_morpho_and_euler_earn_vaults_are_permissionless() -> None:
    """Test canonical adapters do not invent a KYC requirement.

    1. Construct adapters whose policy does not need an RPC read.
    2. Query their vault-wide deposit permission.
    3. Confirm each canonical protocol is permissionless.
    """
    # 1. Construct adapters whose policy does not need an RPC read.
    vaults = (
        object.__new__(MorphoV1Vault),
        object.__new__(EulerEarnVault),
    )

    # 2. Query their vault-wide deposit permission.
    permissions = [vault.is_whitelisted_deposit() for vault in vaults]

    # 3. Confirm each canonical protocol is permissionless.
    assert permissions == [False, False]


def test_euler_evk_hook_policy() -> None:
    """Test EVK distinguishes open, disabled, and custom-hook deposits.

    1. Supply representative canonical hook configurations.
    2. Query deposit permissions for open and globally disabled operations.
    3. Confirm an arbitrary custom hook remains explicitly unknown.
    """
    # 1. Supply representative canonical hook configurations.
    vault = object.__new__(EulerVault)
    custom_hook = "0x1111111111111111111111111111111111111111"

    # 2. Query deposit permissions for open and globally disabled operations.
    with patch.object(EulerVault, "fetch_hook_config", return_value=(ZERO_ADDRESS_STR, 0)):
        assert vault.is_whitelisted_deposit() is False
    with patch.object(EulerVault, "fetch_hook_config", return_value=(ZERO_ADDRESS_STR, EULER_OP_DEPOSIT)):
        assert vault.is_whitelisted_deposit() is False

    # 3. Confirm an arbitrary custom hook remains explicitly unknown.
    with (
        patch.object(EulerVault, "fetch_hook_config", return_value=(custom_hook, EULER_OP_DEPOSIT)),
        patch.object(EulerVault, "address", custom_hook),
        pytest.raises(NotImplementedError, match="custom deposit hooks"),
    ):
        vault.is_whitelisted_deposit()


def test_morpho_v2_gate_policy() -> None:
    """Test Morpho V2 distinguishes canonical open vaults from custom gates.

    1. Supply zero and non-zero gate configurations.
    2. Query the canonical ungated vault policy.
    3. Confirm an arbitrary custom gate remains explicitly unknown.
    """
    # 1. Supply zero and non-zero gate configurations.
    vault = object.__new__(MorphoV2Vault)
    custom_gate = "0x1111111111111111111111111111111111111111"

    # 2. Query the canonical ungated vault policy.
    with patch.object(MorphoV2Vault, "fetch_deposit_gates", return_value=(ZERO_ADDRESS_STR, ZERO_ADDRESS_STR)):
        assert vault.is_whitelisted_deposit() is False

    # 3. Confirm an arbitrary custom gate remains explicitly unknown.
    with (
        patch.object(MorphoV2Vault, "fetch_deposit_gates", return_value=(custom_gate, ZERO_ADDRESS_STR)),
        patch.object(MorphoV2Vault, "address", custom_gate),
        pytest.raises(NotImplementedError, match="custom gates"),
    ):
        vault.is_whitelisted_deposit()

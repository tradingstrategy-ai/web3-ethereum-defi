"""Unit coverage for the requested Morpho and Euler whitelist assumption."""

import pytest

from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault, EulerVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.vault.deposit_redeem import PERMISSIONED_HOOK_CHECKS_NOT_PERFORMED_NOTE


@pytest.mark.parametrize("vault_class", (MorphoV1Vault, MorphoV2Vault, EulerVault, EulerEarnVault))
def test_morpho_and_euler_vaults_use_requested_whitelist_assumption(vault_class: type) -> None:
    """Every supported Morpho and Euler adapter exposes the requested caveat."""
    vault = object.__new__(vault_class)
    assert vault.is_whitelisted_deposit() is True
    assert vault.get_whitelist_notes() == PERMISSIONED_HOOK_CHECKS_NOT_PERFORMED_NOTE

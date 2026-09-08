"""Test canonical Yearn frontend links."""

import pytest

from eth_defi.erc_4626.vault_protocol.yearn.compounder import YearnCompounderVault
from eth_defi.erc_4626.vault_protocol.yearn.links import create_yearn_vault_link
from eth_defi.erc_4626.vault_protocol.yearn.morpho_compounder import YearnMorphoCompounderStrategy
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.vault.base import VaultSpec

VAULT_ADDRESS = "0x254bd33e2f62713f893f0842c99e68f855cda315"
EXPECTED_LINK = f"https://yearn.fi/vaults/1/{VAULT_ADDRESS}"


def test_create_yearn_vault_link_uses_current_frontend_route() -> None:
    """Yearn links use the shared current chain-and-address route."""

    assert create_yearn_vault_link(1, VAULT_ADDRESS) == EXPECTED_LINK


@pytest.mark.parametrize("vault_class", (YearnV3Vault, YearnCompounderVault, YearnMorphoCompounderStrategy))
def test_all_yearn_adapters_delegate_to_the_shared_link_builder(vault_class: type) -> None:
    """Every supported Yearn vault family returns the same canonical link."""

    vault = object.__new__(vault_class)
    vault.spec = VaultSpec(1, VAULT_ADDRESS)

    assert vault.get_link() == EXPECTED_LINK

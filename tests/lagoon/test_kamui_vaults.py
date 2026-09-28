"""Current-state integration coverage for Kamui's Lagoon vaults."""

import os

import pytest

from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.lagoon.constants import KAMUI_LAGOON_VAULTS
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")

#: Public product names expected from the reviewed Kamui contracts.
KAMUI_VAULT_NAMES = {
    "0xcda323c2df692d989b24ba51d0acca924cf9a344": "Stable Vault",
    "0xa5ae405242f42c47996a0c6857ff10a77f9bdee6": "Balanced Vault",
    "0x9e0db8f43bb91e2148b0db920e21370525cf3aab": "Boosted Vault",
}

#: Unapproved wallet used to prove that the vault-wide whitelist is enforced.
UNAPPROVED_ACCOUNT = "0xa2b04c6a053ab2efbc699f5dd0f0957742a41629"


@pytest.mark.parametrize(("chain_id", "vault_address"), sorted(KAMUI_LAGOON_VAULTS))
def test_kamui_lagoon_vault_listing_and_deposit_permissions(chain_id: int, vault_address: str) -> None:
    """Verify Kamui's listing eligibility and permissioned access mode onchain.

    Kamui launched after the repository's canonical fixed Ethereum fork block,
    so this minimal current-state integration test reads the configured provider
    directly. It exercises the same Lagoon adapter and permission reader used by
    the production metadata scanner.

    :param chain_id:
        Ethereum chain ID from the reviewed Kamui registry.

    :param vault_address:
        Reviewed Kamui Lagoon vault address.

    :return:
        ``None``. Assertions validate live contract and listing metadata.
    """

    assert JSON_RPC_ETHEREUM is not None
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM, expected_chain_id=chain_id)
    vault = LagoonVault(web3, VaultSpec(chain_id=chain_id, vault_address=vault_address))

    assert vault.name == KAMUI_VAULT_NAMES[vault_address]
    assert vault.version is LagoonVersion.v_0_6_0
    assert fetch_deposit_permission(vault) is VaultDepositPermission.whitelisted
    assert vault.is_account_whitelisted(UNAPPROVED_ACCOUNT) is False
    assert VaultFlag.unofficial not in vault.get_flags()
    assert identify_curator(chain_id, vault.symbol, vault.name, vault_address, "lagoon-finance") == "kamui"

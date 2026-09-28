"""Fixed-block access-policy checks for reviewed private Arbitrum Lagoon vaults."""

import os

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultSpec

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]

#: Neutral account used to confirm the disabled v0.5 whitelist admits arbitrary accounts.
REPORT_CALLER = "0xa2b04c6a053ab2efbc699f5dd0f0957742a41629"


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create a shared fixed-block Arbitrum fork for permission reads.

    :param anvil_fork_pool:
        Session-scoped shared fork manager.
    :return:
        Web3 connected to the canonical Arbitrum midnight fork.
    """

    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.mark.parametrize(
    "vault_address",
    (
        "0x1723cb57af58efb35a013870c90fcc3d60174a4e",
        "0xc047d64dafe9e6ac76508835c17c6719f9278c1c",
    ),
)
def test_reviewed_private_arbitrum_lagoon_vaults_are_permissionless(web3: Web3, vault_address: str) -> None:
    """Confirm both reviewed Arbitrum vaults have disabled v0.5 whitelists."""

    vault = LagoonVault(web3, VaultSpec(chain_id=42161, vault_address=vault_address), default_block_identifier=ARBITRUM_MIDNIGHT_BLOCK)

    assert vault.version is LagoonVersion.v_0_5_0
    assert vault.is_whitelisted_deposit() is False
    assert vault.is_account_whitelisted(REPORT_CALLER) is True

"""Fixed-block regression coverage for Lagoon v0.6 version detection.

This test retains coverage for the official v0.6 ABI and modern access view.
The separate Base test module characterises the production v1 deployment that
prompted the current scanner compatibility change.
"""

import os

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultSpec

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]

#: Known Lagoon v0.6 vault used for the Ethereum fixed-block regression.
LAGOON_V060_VAULT: HexAddress = "0xd0c4c9386f7509c44987f43136be7d4349ccddc9"


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create Web3 on the shared canonical Ethereum fork.

    The shared fixture and fixed block allow CI to reuse the committed warm
    RPC cache instead of opening a mutable latest-block fork.

    :return:
        Web3 connection backed by the pooled Anvil process.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


def test_lagoon_v060_version_detection(web3: Web3) -> None:
    """Detect v0.6 and read its official ABI and access-policy surface.

    Exact metadata and policy values ensure version routing remains compatible
    with this deployment at the repository's canonical Ethereum fork block.
    """
    spec = VaultSpec(1, LAGOON_V060_VAULT)
    vault = LagoonVault(web3, spec, default_block_identifier=ETHEREUM_MIDNIGHT_BLOCK)

    assert vault.version == LagoonVersion.v_0_6_0
    assert vault.vault_abi == "lagoon/v0.6.0/Vault.json"
    assert vault.name == "9Summits Flagship EURC"
    assert vault.symbol == "9SEURC"
    assert vault.denomination_token.symbol == "EURC"

    # The canonical v0.6 contract uses ``isAllowed(address)``. This deployment
    # is in the default-open access mode at the fixed block.
    assert vault.is_account_whitelisted(ZERO_ADDRESS_STR) is True
    assert vault.is_whitelisted_deposit() is False

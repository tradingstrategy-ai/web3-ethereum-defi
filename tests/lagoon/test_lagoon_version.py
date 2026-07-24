"""Test Lagoon vault version detection.

- Lagoon deploys new contract versions over time (v0.4.0, v0.5.0, v0.6.0, ...)
- Our scanner must handle each known version without crashing
- v0.6.0 was first seen on Ethereum mainnet (9Summits Flagship EURC)
  and caused NotImplementedError in production (scan_all_chains)
"""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)

#: 9Summits Flagship EURC — first known Lagoon v0.6.0 vault on Ethereum mainnet
LAGOON_V060_VAULT = "0xd0c4c9386f7509c44987f43136be7d4349ccddc9"


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum mainnet."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=25_121_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork) -> Web3:
    """Create web3 connection to the forked Ethereum."""
    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)


@flaky.flaky
def test_lagoon_v060_version_detection(web3: Web3):
    """Lagoon v0.6.0 vaults must be detected without crashing.

    Previously, the scanner raised NotImplementedError for unknown Lagoon
    versions, which broke the entire scan_all_chains pipeline.

    1. Create a LagoonVault for the known v0.6.0 contract
    2. Verify the version is correctly detected as v_0_6_0
    3. Verify the vault ABI is loaded (uses v0.5.0 ABI as a compatible fallback)
    4. Verify basic vault properties are readable
    5. Verify the v0.6 ``isAllowed(address)`` access view
    """
    # 1. Create a LagoonVault for the known v0.6.0 contract
    spec = VaultSpec(1, LAGOON_V060_VAULT)
    vault = LagoonVault(web3, spec)

    # 2. Verify the version is correctly detected as v_0_6_0
    assert vault.version == LagoonVersion.v_0_6_0

    # 3. Verify the vault ABI is loaded (uses v0.5.0 ABI as a compatible fallback)
    assert vault.vault_abi == "lagoon/v0.5.0/Vault.json"

    # 4. Verify basic vault properties are readable
    assert vault.name == "9Summits Flagship EURC"
    assert vault.symbol == "9SEURC"
    assert vault.denomination_token.symbol == "EURC"

    # 5. The canonical v0.6 contract replaces isWhitelisted(address) with
    # isAllowed(address). This deployment is in the default-open access mode.
    assert vault.is_account_whitelisted(ZERO_ADDRESS_STR) is True
    assert vault.is_whitelisted_deposit() is False

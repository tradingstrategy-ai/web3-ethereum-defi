"""Test Aera vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.aera.vault import AeraVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)

#: USDC AeraVault Strategy on Ethereum.
AERA_USDC_STRATEGY = "0x6593bb7272237f36444dee44df46ab3b0233a9a0"

#: Aera vaults currently identified by hardcoded addresses.
AERA_VAULT_ADDRESSES = {
    "0x8041ba598f0e656ebe80c67289efb42c09e86ae3",
    "0x6593bb7272237f36444dee44df46ab3b0233a9a0",
    "0x7077ef67fe49ffb1260b893f2cd8475eeb72bbbb",
    "0x00be76740759518db9c51bc59ec1993f2ffa4648",
    "0x83cd3d0e9f027b70cb4833b5c251f6fb62cfd9b0",
}


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=25_301_420)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create Web3 connection to the Anvil fork."""
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


def test_aera_hardcoded_addresses() -> None:
    """Check all known Aera addresses are present in hardcoded classification.

    The first Aera integration deliberately uses address-based classification.
    This test keeps the supported address set visible and prevents accidental
    removal before a generic Aera contract probe exists.
    """
    for address in AERA_VAULT_ADDRESSES:
        assert HARDCODED_PROTOCOLS[address] == {ERC4626Feature.aera_like}


@flaky.flaky
def test_aera(web3: Web3) -> None:
    """Read Aera vault metadata.

    https://etherscan.io/address/0x6593bb7272237f36444dee44df46ab3b0233a9a0
    """
    features = detect_vault_features(web3, AERA_USDC_STRATEGY, verbose=False)
    assert features == {ERC4626Feature.aera_like}

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=AERA_USDC_STRATEGY,
    )

    assert isinstance(vault, AeraVault)
    assert vault.get_protocol_name() == "Aera"
    assert vault.features == {ERC4626Feature.aera_like}

    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_risk() is None
    assert vault.get_link() == "https://app.aera.finance/"

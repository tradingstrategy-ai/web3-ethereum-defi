"""Test Arcus pToken vault protocol support."""

import os

import eth_abi
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_BRIDGE_VAULT, ARCUS_BTC_3X_LONG_VAULT, ARCUS_HOOD_3X_LONG_VAULT
from eth_defi.erc_4626.vault_protocol.arcus.vault import ArcusVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ROBINHOOD_MIDNIGHT_BLOCK
from eth_defi.vault.risk import VaultTechnicalRisk

JSON_RPC_ROBINHOOD = os.environ.get("JSON_RPC_ROBINHOOD")

#: Exact total assets observed at :data:`ROBINHOOD_MIDNIGHT_BLOCK`.
ARCUS_HOOD_3X_LONG_TOTAL_ASSETS = 38_196_951_001

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ROBINHOOD is None, reason="JSON_RPC_ROBINHOOD needed to run these tests"),
    pytest.mark.xdist_group("fork:robinhood:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return a shared Robinhood fork at the canonical post-deployment block.

    This read-only test shares the Robinhood Chain midnight block with future
    characterisation tests and does not need snapshot/revert isolation.

    :param anvil_fork_pool:
        Session-scoped shared Anvil process pool.

    :return:
        Web3 instance connected to the shared Robinhood fork.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ROBINHOOD, ROBINHOOD_MIDNIGHT_BLOCK)


def test_arcus_hood_3x_long_vault(web3: Web3) -> None:
    """Characterise the reviewed Arcus HOOD 3x Long pToken vault."""

    vault = create_vault_instance_autodetect(web3, vault_address=ARCUS_HOOD_3X_LONG_VAULT)

    assert isinstance(vault, ArcusVault)
    assert ERC4626Feature.arcus_like in vault.features
    assert get_vault_protocol_name(vault.features) == "Arcus"
    assert vault.vault_contract.functions.name().call() == "HOOD (3x Long)"
    assert vault.vault_contract.functions.symbol().call() == "pHOOD3x"
    assert vault.vault_contract.functions.asset().call().lower() == "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    assert vault.vault_contract.functions.totalAssets().call() == ARCUS_HOOD_3X_LONG_TOTAL_ASSETS
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_risk() == VaultTechnicalRisk.dangerous
    assert vault.get_fee_mode() is None
    assert vault.get_link() == "https://app.arcus.xyz/"

    assert vault.manager_name is None
    assert vault.short_description == "Arcus pToken targeting 3x long HOOD perpetual exposure."
    assert vault.description is not None
    assert "pro-rata claim" in vault.description
    notes = vault.get_notes()
    assert notes is not None
    assert "HOOD perpetual position" in notes
    assert "not simply 3 times" in notes

    bridge_vault_raw = web3.eth.call({"to": Web3.to_checksum_address(ARCUS_HOOD_3X_LONG_VAULT), "data": Web3.keccak(text="bridgeVault()")[:4]})
    bridge_vault = eth_abi.decode(["address"], bridge_vault_raw)[0]
    assert bridge_vault.lower() == ARCUS_BRIDGE_VAULT.lower()


def test_arcus_btc_3x_long_vault(web3: Web3) -> None:
    """Characterise the second reviewed Arcus production pToken."""

    vault = create_vault_instance_autodetect(web3, vault_address=ARCUS_BTC_3X_LONG_VAULT)

    assert isinstance(vault, ArcusVault)
    assert vault.vault_contract.functions.name().call() == "BTC (3x Long)"
    assert vault.vault_contract.functions.symbol().call() == "pBTC3x"
    assert vault.vault_contract.functions.asset().call().lower() == "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    assert vault.manager_name is None
    assert vault.short_description == "Arcus pToken targeting 3x long BTC perpetual exposure."
    notes = vault.get_notes()
    assert notes is not None
    assert "BTC perpetual position" in notes

    bridge_vault_raw = web3.eth.call({"to": Web3.to_checksum_address(ARCUS_BTC_3X_LONG_VAULT), "data": Web3.keccak(text="bridgeVault()")[:4]})
    bridge_vault = eth_abi.decode(["address"], bridge_vault_raw)[0]
    assert bridge_vault.lower() == ARCUS_BRIDGE_VAULT.lower()

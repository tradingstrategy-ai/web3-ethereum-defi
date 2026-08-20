"""Pallas vault characterisation tests."""

import os

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance_autodetect  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
from eth_defi.erc_4626.vault_protocol.pallas.constants import PALLAS_BASIS_TRADING_HIP_3_VAULT, PALLAS_DIRECTIONAL_VOLATILITY_VAULT, PALLAS_HARDCODED_LEADS
from eth_defi.erc_4626.vault_protocol.pallas.vault import PallasVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import HYPERLIQUID_MIDNIGHT_BLOCK

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = [
    # Shared with other Hyperliquid characterisation tests at the canonical block.
    pytest.mark.xdist_group("fork:hyperliquid:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return Web3 backed by the shared read-only HyperEVM fork.

    The Basis Trading HIP-3 vault existed at the canonical shared block. The
    Directional Volatility deployment is newer, so its hardcoded chain-aware
    classification is checked without trying to read code before deployment.

    :param anvil_fork_pool:
        Session-scoped shared Anvil fork registry.
    :return:
        Web3 client connected to the shared HyperEVM fork.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_HYPERLIQUID, HYPERLIQUID_MIDNIGHT_BLOCK)


def test_pallas_hardcoded_addresses_are_chain_aware() -> None:
    """Classify only reviewed Pallas addresses on their HyperEVM deployment chain.

    Address-only detection would allow a hypothetical same-address contract on
    another network to be incorrectly labelled as Pallas. This regression test
    keeps the registry safely chain-aware for both reviewed vaults.

    :return:
        ``None``. Assertions validate the address registry.
    """
    expected = {ERC4626Feature.pallas_like}

    assert _get_hardcoded_protocol_features(PALLAS_BASIS_TRADING_HIP_3_VAULT, chain_id=999) == expected
    assert _get_hardcoded_protocol_features(PALLAS_DIRECTIONAL_VOLATILITY_VAULT, chain_id=999) == expected
    assert _get_hardcoded_protocol_features(PALLAS_BASIS_TRADING_HIP_3_VAULT, chain_id=1) is None
    assert _get_hardcoded_protocol_features(PALLAS_DIRECTIONAL_VOLATILITY_VAULT, chain_id=1) is None


@pytest.mark.skipif(JSON_RPC_HYPERLIQUID is None, reason="JSON_RPC_HYPERLIQUID needed to run this test")
def test_pallas_basis_trading_hip_3(web3: Web3) -> None:
    """Read the Pallas Basis Trading HIP-3 vault at the fixed shared fork block.

    This confirms the hardcoded deployment receives the Pallas adapter while
    preserving the contract's asynchronous lifecycle: no generic synchronous
    deposit or redemption capability is advertised.

    :param web3:
        Web3 client connected to the fixed HyperEVM fork.
    :return:
        ``None``. Assertions validate adapter metadata and public capability.
    """
    vault = create_vault_instance_autodetect(web3, vault_address=PALLAS_BASIS_TRADING_HIP_3_VAULT)

    assert isinstance(vault, PallasVault)
    assert vault.features == {ERC4626Feature.pallas_like}
    assert vault.get_protocol_name() == "Pallas"
    assert vault.name == "Pallas Vault Share"
    assert vault.symbol == "PALLAS"
    assert vault.denomination_token.symbol == "USD₮0"
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_estimated_lock_up() is None
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_link() == "https://app.pallas.fund/vault/basis-trading-hip-3"


def test_pallas_hardcoded_leads_are_enabled_for_default_discovery() -> None:
    """Keep reviewed Pallas deployments available to default lead discovery.

    Hardcoded leads are needed because ERC-7540 queue events need not be
    emitted by the vault proxy using ordinary ERC-4626 event signatures.

    :return:
        ``None``. Assertion validates default discovery source registration.
    """

    assert ("Pallas", PALLAS_HARDCODED_LEADS) in DEFAULT_HARDCODED_VAULT_LEAD_SOURCES

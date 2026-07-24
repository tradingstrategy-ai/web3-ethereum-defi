"""Test BaseVol vault metadata."""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.basevol.vault import BaseVolVault
from eth_defi.vault.base import VaultTechnicalRisk

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import BASE_MIDNIGHT_BLOCK

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests"),
    # Shared with the other Base midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:base:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Base fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, BASE_MIDNIGHT_BLOCK)


@flaky.flaky
def test_basevol(
    web3: Web3,
    tmp_path: Path,
):
    """Read BaseVol Genesis Vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf1BE2622fd0f34d520Ab31019A4ad054a2c4B1e0",
    )

    assert isinstance(vault, BaseVolVault)
    assert vault.get_protocol_name() == "BaseVol"
    assert vault.features == {ERC4626Feature.basevol_like}
    assert vault.get_risk() == VaultTechnicalRisk.severe
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_link() == "https://basevol.com/"
    assert vault.name == "Genesis Vault"
    assert vault.denomination_token.symbol == "USDC"

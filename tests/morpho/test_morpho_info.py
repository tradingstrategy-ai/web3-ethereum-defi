"""Morpho Base mainnet fork based tests.

- Read various information out of the vault
"""
import os

import pytest
from web3 import Web3

from eth_defi.morpho.vault import MorphoVault
from eth_defi.provider.multi_provider import create_multi_provider_web3

from eth_defi.vault.base import VaultSpec

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope='module')
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


@pytest.fixture(scope='module')
def test_block_number() -> int:
    return 28355690


@pytest.fixture()
def vault(web3) -> MorphoVault:
    spec = VaultSpec(8545, "0x6b13c060F13Af1fdB319F52315BbbF3fb1D88844")
    return MorphoVault(web3, spec)


def test_morpho_fee(
    web3: Web3,
    vault: MorphoVault,
    test_block_number,
):
    """Read Morpho vault fees."""
    block_number = test_block_number
    assert vault.get_management_fee(block_number) == 0
    assert vault.get_performance_fee(block_number) == 0.10

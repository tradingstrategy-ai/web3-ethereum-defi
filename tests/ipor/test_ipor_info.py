"""IPOR Base mainnet fork based tests.

- Read various information out of the vault
"""
import os

import pytest
from web3 import Web3

from eth_defi.ipor.vault import IPORVault
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
    return 27975506


@pytest.fixture()
def vault(web3) -> IPORVault:
    """TODO: Optimise test speed - fetch vault data only once per this module"""
    spec = VaultSpec(8545, "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216")
    return IPORVault(web3, spec)


def test_ipor_fee(
    web3: Web3,
    vault: IPORVault,
    test_block_number,
):
    """Read IPOR vault fees."""
    block_number = test_block_number
    assert vault.get_management_fee(block_number) == 0.01
    assert vault.get_performance_fee(block_number) == 0.10

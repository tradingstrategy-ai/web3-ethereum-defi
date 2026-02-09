"""Morpho Base mainnet fork based tests.

- Read various information out of the vault
"""

import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature, is_lending_protocol
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1VaultHistoricalReader, MorphoVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


@pytest.fixture(scope="module")
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


def test_morpho_v1_utilisation(
    web3: Web3,
    vault: MorphoVault,
):
    """Test Morpho V1 utilisation API."""
    # Test lending protocol identification (vault fixture doesn't have features set,
    # so we test directly with the known feature)
    assert is_lending_protocol({ERC4626Feature.morpho_like}) is True

    # Test utilisation API
    available_liquidity = vault.fetch_available_liquidity()
    assert available_liquidity is not None
    assert available_liquidity >= Decimal(0)

    utilisation = vault.fetch_utilisation_percent()
    assert utilisation is not None
    assert 0.0 <= utilisation <= 1.0

    # Test historical reader
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, MorphoV1VaultHistoricalReader)
    calls = list(reader.construct_multicalls())
    call_names = [c.extra_data.get("function") for c in calls if c.extra_data]
    assert "idle_assets" in call_names

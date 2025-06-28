"""IPOR Base mainnet fork based tests.

- Read vault redemption delay
"""
import datetime
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
    return 32155807


@pytest.fixture()
def vault(web3) -> IPORVault:
    """TODO: Optimise test speed - fetch vault data only once per this module"""
    spec = VaultSpec(8545, "0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4")
    return IPORVault(web3, spec)



def test_ipor_redemption_delay(
    web3: Web3,
    vault: IPORVault,
    test_block_number,
):
    """Read IPOR vault redemption delay."""

    # Harvest USDC Autopilot
    # REDEMPTION_DELAY_IN_SECONDS = 1
    # https://basescan.org/address/0x187937aab9b2d57D606D0C3fB98816301fcE0d1f#readContract
    delay = vault.get_redemption_delay()
    assert delay == datetime.timedelta(seconds=1)


"""Read ERC-4626 data using Vault class"""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626VaultInfo, ERC4626Vault
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


@pytest.fixture(scope='module')
def ipor_usdc_address() -> HexAddress:
    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    return "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216"


@pytest.fixture(scope='module')
def vault(web3, ipor_usdc_address) -> ERC4626VaultInfo:
    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    spec = VaultSpec(web3.eth.chain_id, ipor_usdc_address)
    return ERC4626Vault(web3, spec)


def test_4626_read_info(
    vault: ERC4626Vault,
    test_block_number: int,
):
    """Read IPOR USDC Base vault"""
    assert vault.denomination_token.symbol == "USDC"
    assert vault.share_token.symbol == "ipUSDCfusion"
    assert vault.fetch_total_assets(block_identifier=test_block_number) == Decimal('1437072.77357')
    assert vault.fetch_total_supply(block_identifier=test_block_number) == Decimal('1390401.22652875')



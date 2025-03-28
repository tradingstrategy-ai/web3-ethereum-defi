import os

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626VaultInfo, ERC4626Vault
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import fetch_erc20_details, USDC_NATIVE_TOKEN, SUSDS_NATIVE_TOKEN
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import VaultHistoricalReadMulticaller

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




def test_4626_historical_returns(
    web3: Web3,
    vault: ERC4626Vault,
):
    """Construct historical returns of IPOR USDC vault."""

    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    ipor_usdc = ERC4626Vault(web3, VaultSpec(web3.eth.chain_id, "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216"))

    # https://app.morpho.org/base/vault/0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca/moonwell-flagship-usdc
    moonwell_flagship_usdc = ERC4626Vault(web3, VaultSpec(web3.eth.chain_id, "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"))

    # https://www.superform.xyz/vault/Dgrw4wBA1YgfvI2BxA8YN/
    steakhouse_susds = ERC4626Vault(web3, VaultSpec(web3.eth.chain_id, "0xB17B070A56043e1a5a1AB7443AfAFDEbcc1168D7"))

    vaults = [
        ipor_usdc,
        moonwell_flagship_usdc,
        steakhouse_susds,
    ]

    start = 15_000_000
    end = 27_000_000

    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[web3.eth.chain_id])
    susds = fetch_erc20_details(web3, SUSDS_NATIVE_TOKEN[web3.eth.chain_id])

    reader = VaultHistoricalReadMulticaller(
        web3factory=MultiProviderWeb3Factory(JSON_RPC_BASE),
        supported_quote_tokens={usdc, susds}
    )

    records = reader.read_historical(
        vaults=vaults,
        start_block=start,
        end_block=end,
        # Base has block time of 2 sceonds
        step=24*3600 // 2,
    )

    records = list(records)
    assert len(records) == 1






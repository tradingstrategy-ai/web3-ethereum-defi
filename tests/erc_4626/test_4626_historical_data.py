"""Test ERC-4626 vault data pollers, share price, etc."""

import datetime
import os
from decimal import Decimal

import pytest

from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.ipor.vault import IPORVault
from eth_defi.morpho.vault import MorphoVault
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import fetch_erc20_details, USDC_NATIVE_TOKEN, SUSDS_NATIVE_TOKEN
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import VaultHistoricalReadMulticaller

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


def test_4626_historical_vault_data(
    web3: Web3,
):
    """Read historical data of IPOR USDC and some other vaults."""

    # https://app.ipor.io/fusion/base/0x45aa96f0b3188d47a1dafdbefce1db6b37f58216
    ipor_usdc = IPORVault(web3, VaultSpec(web3.eth.chain_id, "0x45aa96f0b3188d47a1dafdbefce1db6b37f58216"))

    # https://app.morpho.org/base/vault/0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca/moonwell-flagship-usdc
    moonwell_flagship_usdc = MorphoVault(web3, VaultSpec(web3.eth.chain_id, "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"))

    # https://www.superform.xyz/vault/Dgrw4wBA1YgfvI2BxA8YN/
    steakhouse_susds = ERC4626Vault(web3, VaultSpec(web3.eth.chain_id, "0xB17B070A56043e1a5a1AB7443AfAFDEbcc1168D7"))

    vaults = [
        ipor_usdc,
        moonwell_flagship_usdc,
        steakhouse_susds,
    ]

    # When IPOR vault was deployed https://basescan.org/tx/0x65e66f1b8648a880ade22e316d8394ed4feddab6fc0fc5bbc3e7128e994e84bf
    start = 22_140_976
    end = 24_000_000

    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[web3.eth.chain_id])
    susds = fetch_erc20_details(web3, SUSDS_NATIVE_TOKEN[web3.eth.chain_id])

    reader = VaultHistoricalReadMulticaller(
        web3factory=MultiProviderWeb3Factory(JSON_RPC_BASE),
        supported_quote_tokens={usdc, susds},
    )

    records = reader.read_historical(
        vaults=vaults,
        start_block=start,
        end_block=end,
        # Base has block time of 2 sceonds
        step=24 * 3600 // 2,
    )

    records = list(records)
    assert len(records) == 132

    # Records are not guaranteed to be in specific order, so fix it here
    records.sort(key=lambda r: (r.block_number, r.vault.address))

    r = records[0]
    assert r.block_number == 22140976
    assert r.timestamp == datetime.datetime(2024, 11, 8, 13, 8, 19)
    assert r.vault.name == "IPOR USDC Lending Optimizer Base"
    assert r.total_assets == 0
    assert r.total_supply == 0
    assert r.share_price is None

    r = records[-1]
    assert r.block_number == 23998576
    assert r.timestamp == datetime.datetime(2024, 12, 21, 13, 8, 19)
    assert r.vault.name == "Moonwell Flagship USDC"
    assert r.total_assets == Decimal("37404103.569505")
    assert r.total_supply == Decimal("37003383.191686681452465622")
    assert r.share_price == pytest.approx(Decimal("1.0108292902771210900318"))
    assert r.management_fee == 0
    assert r.performance_fee == 0.15

    r = records[-3]
    assert r.block_number == 23998576
    assert r.timestamp == datetime.datetime(2024, 12, 21, 13, 8, 19)
    assert r.vault.name == "IPOR USDC Lending Optimizer Base"
    assert r.total_assets == Decimal("1343875.946355")
    assert r.total_supply == Decimal("1327724.55695781")
    assert r.share_price == pytest.approx(Decimal("1.012164713917920875873501"))
    assert r.performance_fee == 0.10
    assert r.management_fee == 0.01

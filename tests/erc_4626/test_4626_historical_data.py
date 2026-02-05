"""Test ERC-4626 vault data pollers, share price, etc."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault, VaultReaderState
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoVault
from eth_defi.event_reader.multicall_batcher import read_multicall_historical_stateful
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import SUSDS_NATIVE_TOKEN, USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import VaultHistoricalReadMulticaller

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    return web3


def test_4626_historical_vault_data_stateless(
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
    assert len(records) == 79

    # Records are not guaranteed to be in specific order, so fix it here
    records.sort(key=lambda r: (r.block_number, r.vault.address))

    r = records[0]
    assert r.block_number == 22140976
    assert r.timestamp == datetime.datetime(2024, 11, 8, 13, 8, 19)
    assert r.vault.name == "IPOR USDC Lending Optimizer Base"
    assert r.total_assets == 0
    assert r.total_supply == 0
    assert r.share_price == Decimal(1)
    # max_deposit should be populated from base ERC-4626 reader
    assert r.max_deposit is not None
    assert r.max_redeem is None

    r = [r for r in records if r.vault.name == "Moonwell Flagship USDC"][-1]
    assert r.block_number == 23998576
    assert r.timestamp == datetime.datetime(2024, 12, 21, 13, 8, 19)
    assert r.vault.name == "Moonwell Flagship USDC"
    # AssertionError: assert Decimal('37003383.191686681452465622') == Decimal('37404103.569505')
    assert r.total_assets == Decimal("37404103.569505")
    assert r.total_supply == Decimal("37003383.191686681452465622")
    assert r.share_price == pytest.approx(Decimal("1.0108292902771210900318"))
    assert r.management_fee == 0
    assert r.performance_fee == 0.15
    assert r.max_deposit is not None
    assert r.max_redeem is None
    # Base reader does not set protocol-specific boolean flags
    assert r.deposits_open is None
    assert r.redemption_open is None
    assert r.trading is None


# 5
# FAILED tests/erc_4626/test_4626_historical_data.py::test_4626_historical_vault_data_stateful - requests.exceptions.ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
@flaky.flaky
def test_4626_historical_vault_data_stateful(
    web3: Web3,
    tmp_path,
):
    """Read historical data of IPOR USDC and some other vaults using a stateful reader."""

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
    start = 23_800_000
    end = 24_000_000

    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[web3.eth.chain_id])
    susds = fetch_erc20_details(web3, SUSDS_NATIVE_TOKEN[web3.eth.chain_id])

    timestamp_cache_path = tmp_path / "timestamp_cache"

    reader = VaultHistoricalReadMulticaller(
        web3factory=MultiProviderWeb3Factory(JSON_RPC_BASE),
        supported_quote_tokens={usdc, susds},
        timestamp_cache_file=timestamp_cache_path,
    )

    records = reader.read_historical(
        vaults=vaults,
        start_block=start,
        end_block=end,
        # Base has block time of 2 second
        step=1 * 3600 // 2,  # The base frequency is 1 hour, but should_invoke() can disable it or drop to 24h
        reader_func=read_multicall_historical_stateful,
    )

    records = list(records)

    # We read 3 vaults, so we should have 3 stateful readers
    vault_readers = reader.readers
    assert len(vault_readers) == 3

    # Ipor
    state = vault_readers["0x45aa96f0b3188D47a1DaFdbefCE1db6B37f58216"].reader_state
    assert state.last_call_at == datetime.datetime(2024, 12, 21, 13, 49, 7)
    assert state.first_read_at == datetime.datetime(2024, 12, 16, 22, 49, 7)
    assert state.first_seen_at_block is None  # Never passed as arg
    assert state.max_tvl == pytest.approx(Decimal("1343887.145555"))
    assert state.peaked_at is None
    assert state.faded_at is None
    assert state.get_frequency() == ("large_tvl", datetime.timedelta(hours=1))
    assert state.last_share_price == pytest.approx(Decimal("1.012172"))

    # Steak
    # Deployed at 26_598_326
    # No data
    state = vault_readers["0xB17B070A56043e1a5a1AB7443AfAFDEbcc1168D7"].reader_state
    assert state.entry_count == 75
    assert state.vault.name == "Steakhouse sUSDS"
    assert state.get_frequency() == ("early", datetime.timedelta(days=1))

    # Test serialisation: IPOR
    state = vault_readers["0x45aa96f0b3188D47a1DaFdbefCE1db6B37f58216"].reader_state
    saved = state.save()
    assert saved["max_tvl"] == pytest.approx(Decimal("1343887.145555"))
    alternative_state = VaultReaderState(vaults[0])
    alternative_state.load(saved)
    assert alternative_state.max_tvl == pytest.approx(Decimal("1343887.145555"))

    # Many more records than with the daily scanner above because we read every hour
    assert len(records) == 71

    # Records are not guaranteed to be in specific order, so fix it here
    records.sort(key=lambda r: (r.block_number, r.vault.address))

    r = [r for r in records if r.vault.name == "Moonwell Flagship USDC"][-1]
    assert r.block_number == 23998000
    assert r.management_fee == 0
    assert r.performance_fee == 0.15
    assert r.vault.name == "Moonwell Flagship USDC"

    r = [r for r in records if r.vault.name == "IPOR USDC Lending Optimizer Base"][-1]
    assert r.block_number == 23962000
    assert r.vault.name == "IPOR USDC Lending Optimizer Base"
    assert r.performance_fee == 0.10
    assert r.management_fee == 0.01
    assert r.max_deposit is not None
    assert r.max_redeem is None

    # Verify export round-trip includes new fields
    exported = r.export()
    assert "max_deposit" in exported
    assert "max_redeem" in exported
    assert "deposits_open" in exported
    assert "redemption_open" in exported
    assert "trading" in exported
    assert exported["deposits_open"] == ""  # None -> empty string for non-protocol-specific vaults
    assert exported["trading"] == ""

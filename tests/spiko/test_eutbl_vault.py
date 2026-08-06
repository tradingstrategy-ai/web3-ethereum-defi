"""Test Spiko EUTBL tracking on a fixed shared Arbitrum fork."""

import datetime
import os
from decimal import Decimal

import pandas as pd
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, _get_hardcoded_protocol_features, create_vault_instance_autodetect  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.tokenised_fund.spiko.constants import EUTBL_FIRST_SEEN_AT, EUTBL_FIRST_SEEN_AT_BLOCK, EUTBL_MANAGEMENT_FEE, EUTBL_TOKEN_ADDRESS, USTBL_TOKEN_ADDRESS
from eth_defi.tokenised_fund.spiko.historical import SpikoHistoricalReader, SpikoVaultReaderState
from eth_defi.tokenised_fund.spiko.vault import SPIKO_PERMISSIONED_FLOW_REASON, SpikoVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import identify_curator, is_protocol_curator
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.vaultdb import VaultDatabase

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

EXPECTED_TOTAL_SUPPLY = Decimal("335544786.99289")
EXPECTED_SOURCE_SHARE_PRICE = Decimal("1.05505")
EXPECTED_USD_EXCHANGE_RATE = Decimal("1.137955")
EXPECTED_SHARE_PRICE = Decimal("1.20059942275")
EXPECTED_TOTAL_ASSETS = Decimal("402854877.5704354423542475")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run Spiko EUTBL integration tests"),
    # Share the canonical Arbitrum midnight fork across read-only tests.
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create Web3 backed by the shared fixed Arbitrum fork.

    :param anvil_fork_pool:
        Session-scoped shared Anvil fork registry.
    :return:
        Web3 connected to Arbitrum at the canonical midnight block.
    """

    assert JSON_RPC_ARBITRUM is not None
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


def test_eutbl_adapter_and_historical_reader(web3: Web3) -> None:
    """Read EUTBL supply, USD-normalised NAV and restricted lifecycle.

    :param web3:
        Web3 connected to the fixed Arbitrum fork.
    """

    features = HARDCODED_PROTOCOLS[EUTBL_TOKEN_ADDRESS]
    assert features == {ERC4626Feature.spiko_like}
    assert _get_hardcoded_protocol_features(EUTBL_TOKEN_ADDRESS, chain_id=42161) == features
    assert _get_hardcoded_protocol_features(EUTBL_TOKEN_ADDRESS, chain_id=1) is None
    assert _get_hardcoded_protocol_features(USTBL_TOKEN_ADDRESS, chain_id=42161) is None

    vault = create_vault_instance_autodetect(web3, vault_address=EUTBL_TOKEN_ADDRESS)
    assert isinstance(vault, SpikoVault)
    assert vault.features == {ERC4626Feature.spiko_like}
    assert vault.name == "Spiko EU T-Bills Money Market Fund"
    assert vault.symbol == "EUTBL"
    assert vault.fetch_total_supply(ARBITRUM_MIDNIGHT_BLOCK) == EXPECTED_TOTAL_SUPPLY
    assert vault.convert_raw_share_price(1_055_050) == EXPECTED_SOURCE_SHARE_PRICE
    assert vault.convert_raw_usd_exchange_rate(113_795_500) == EXPECTED_USD_EXCHANGE_RATE
    assert vault.fetch_share_price(ARBITRUM_MIDNIGHT_BLOCK) == EXPECTED_SHARE_PRICE
    assert vault.fetch_total_assets(ARBITRUM_MIDNIGHT_BLOCK) == EXPECTED_TOTAL_ASSETS
    assert vault.fetch_deposit_closed_reason() == SPIKO_PERMISSIONED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == SPIKO_PERMISSIONED_FLOW_REASON
    assert vault.get_fee_data().fee_mode is VaultFeeMode.internalised_skimming
    assert vault.get_fee_data().management == EUTBL_MANAGEMENT_FEE
    assert vault.fetch_info()["source_denomination"] == "EUR"
    assert vault.fetch_info()["synthetic_usd_denomination"] is True

    reader = vault.get_historical_reader(stateful=True)
    assert isinstance(reader, SpikoHistoricalReader)
    assert isinstance(reader.reader_state, SpikoVaultReaderState)
    results = [call.call_as_result(web3, block_identifier=ARBITRUM_MIDNIGHT_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(ARBITRUM_MIDNIGHT_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    for result in results:
        result.timestamp = timestamp
    read = reader.process_result(ARBITRUM_MIDNIGHT_BLOCK, timestamp, results)
    assert read.share_price == EXPECTED_SHARE_PRICE
    assert read.total_supply == EXPECTED_TOTAL_SUPPLY
    assert read.total_assets == EXPECTED_TOTAL_ASSETS
    assert read.errors is None
    assert reader.reader_state.exchange_rate == Decimal(1)

    detection = ERC4262VaultDetection(chain=42161, address=EUTBL_TOKEN_ADDRESS, first_seen_at_block=EUTBL_FIRST_SEEN_AT_BLOCK, first_seen_at=EUTBL_FIRST_SEEN_AT, features=features, updated_at=EUTBL_FIRST_SEEN_AT, deposit_count=0, redeem_count=0)
    record = create_vault_scan_record(web3, detection, block_identifier=ARBITRUM_MIDNIGHT_BLOCK, token_cache={})
    assert record["Protocol"] == "Spiko"
    assert record["NAV"] == EXPECTED_TOTAL_ASSETS
    assert record["Denomination"] == "USD"
    assert record["_denomination_token"]["address"] is None
    assert record["_nav_source"] == "spiko_eutbl_oracle_latestRoundData"
    assert record["_spiko_source_denomination"] == "EUR"
    assert record["_source_denomination"] == "EUR"
    assert record["_synthetic_denomination"] is True
    assert record["_synthetic_usd_denomination"] is True
    curator = identify_curator(42161, "EUTBL", "Spiko EU T-Bills Money Market Fund", EUTBL_TOKEN_ADDRESS, "spiko")
    assert curator == "spiko-curator"
    assert is_protocol_curator(curator)


def test_eutbl_top_vault_export_preserves_eur_source_denomination(web3: Web3) -> None:
    """Export EUTBL as USD-valued while retaining its EUR fund currency.

    :param web3:
        Web3 connected to the fixed Arbitrum fork.
    """

    detection = ERC4262VaultDetection(
        chain=42161,
        address=EUTBL_TOKEN_ADDRESS,
        first_seen_at_block=EUTBL_FIRST_SEEN_AT_BLOCK,
        first_seen_at=EUTBL_FIRST_SEEN_AT,
        features={ERC4626Feature.spiko_like},
        updated_at=EUTBL_FIRST_SEEN_AT,
        deposit_count=0,
        redeem_count=0,
    )
    scan_record = create_vault_scan_record(web3, detection, block_identifier=ARBITRUM_MIDNIGHT_BLOCK, token_cache={})
    spec = VaultSpec(42161, EUTBL_TOKEN_ADDRESS)
    timestamps = pd.date_range("2026-07-22", periods=3, freq="D")
    prices = pd.DataFrame(
        {
            "chain": [42161] * 3,
            "address": [EUTBL_TOKEN_ADDRESS] * 3,
            "id": [spec.as_string_id()] * 3,
            "block_number": [ARBITRUM_MIDNIGHT_BLOCK] * 3,
            "share_price": [float(EXPECTED_SHARE_PRICE)] * 3,
            "total_assets": [float(EXPECTED_TOTAL_ASSETS)] * 3,
            "total_supply": [float(EXPECTED_TOTAL_SUPPLY)] * 3,
            "event_count": [0] * 3,
            "vault_poll_frequency": ["large_tvl"] * 3,
        },
        index=timestamps,
    )

    metrics = calculate_lifetime_metrics(calculate_hourly_returns_for_all_vaults(prices), VaultDatabase(rows={spec: scan_record}))
    exported = export_lifetime_row(metrics.iloc[0])

    assert exported["denomination"] == "USD"
    assert exported["source_denomination"] == "EUR"

"""Test Spiko USTBL tracking against a fixed Ethereum mainnet fork."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, _get_hardcoded_protocol_features, create_vault_instance_autodetect  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.spiko.constants import USTBL_FIRST_SEEN_AT, USTBL_FIRST_SEEN_AT_BLOCK, USTBL_TOKEN_ADDRESS
from eth_defi.tokenised_fund.spiko.historical import SpikoHistoricalReader, SpikoVaultReaderState
from eth_defi.tokenised_fund.spiko.vault import SPIKO_PERMISSIONED_FLOW_REASON, USTBL_MANAGEMENT_FEE, SpikoVault
from eth_defi.tokenised_fund.vault import TokenisedFundDepositManager
from eth_defi.vault.curator import identify_curator, is_protocol_curator
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

SPIKO_TEST_BLOCK = 25_550_000
EXPECTED_TOTAL_SUPPLY = Decimal("53782226.27927")
EXPECTED_SHARE_PRICE = Decimal("1.029972")
EXPECTED_TOTAL_ASSETS = Decimal("55394187.16531228044")


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at a reproducible USTBL oracle observation.

    :return: Running Anvil fork.
    """
    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run Spiko integration tests")
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=SPIKO_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create Web3 connected to the fixed fork.

    :param anvil_ethereum_fork: Running Anvil fork.
    :return: Multi-provider Web3 instance.
    """
    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_spiko_hardcoded_protocol() -> None:
    """Classify the exact USTBL deployment, but not address reuse elsewhere."""
    features = HARDCODED_PROTOCOLS[USTBL_TOKEN_ADDRESS]
    assert features == {ERC4626Feature.spiko_like}
    assert get_vault_protocol_name(features) == "Spiko"
    assert _get_hardcoded_protocol_features(USTBL_TOKEN_ADDRESS, chain_id=1) == features
    assert _get_hardcoded_protocol_features(USTBL_TOKEN_ADDRESS, chain_id=8453) is None


@flaky.flaky
def test_spiko_adapter_and_historical_reader(web3: Web3) -> None:
    """Read USTBL supply, NAV and its explicitly restricted lifecycle."""
    vault = create_vault_instance_autodetect(web3, vault_address=USTBL_TOKEN_ADDRESS)
    assert isinstance(vault, SpikoVault)
    assert vault.features == {ERC4626Feature.spiko_like}
    assert vault.name == "Spiko US T-Bills Money Market Fund"
    assert vault.symbol == "USTBL"
    assert vault.fetch_total_supply(SPIKO_TEST_BLOCK) == EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_share_price(SPIKO_TEST_BLOCK) == EXPECTED_SHARE_PRICE
    assert vault.fetch_total_assets(SPIKO_TEST_BLOCK) == EXPECTED_TOTAL_ASSETS
    assert vault.fetch_deposit_closed_reason() == SPIKO_PERMISSIONED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == SPIKO_PERMISSIONED_FLOW_REASON
    assert vault.get_fee_data().fee_mode is VaultFeeMode.internalised_skimming
    assert vault.get_fee_data().management == USTBL_MANAGEMENT_FEE
    assert isinstance(vault.get_deposit_manager(), TokenisedFundDepositManager)

    reader = vault.get_historical_reader(stateful=True)
    assert isinstance(reader, SpikoHistoricalReader)
    assert isinstance(reader.reader_state, SpikoVaultReaderState)
    results = [call.call_as_result(web3, block_identifier=SPIKO_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(SPIKO_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    for result in results:
        result.timestamp = timestamp
    read = reader.process_result(SPIKO_TEST_BLOCK, timestamp, results)
    assert read.share_price == EXPECTED_SHARE_PRICE
    assert read.total_supply == EXPECTED_TOTAL_SUPPLY
    assert read.total_assets == EXPECTED_TOTAL_ASSETS
    assert read.errors is None
    assert reader.reader_state.exchange_rate == Decimal(1)
    assert reader.reader_state.last_block == SPIKO_TEST_BLOCK
    assert reader.reader_state.last_tvl == EXPECTED_TOTAL_ASSETS
    assert reader.reader_state.last_share_price == EXPECTED_SHARE_PRICE


@flaky.flaky
def test_spiko_scan_record_and_curator(web3: Web3) -> None:
    """Export USTBL scan and address-scoped curator metadata."""
    detection = ERC4262VaultDetection(chain=1, address=USTBL_TOKEN_ADDRESS, first_seen_at_block=USTBL_FIRST_SEEN_AT_BLOCK, first_seen_at=USTBL_FIRST_SEEN_AT, features={ERC4626Feature.spiko_like}, updated_at=USTBL_FIRST_SEEN_AT, deposit_count=0, redeem_count=0)
    record = create_vault_scan_record(web3, detection, block_identifier=SPIKO_TEST_BLOCK, token_cache={})
    assert record["Protocol"] == "Spiko"
    assert record["NAV"] == EXPECTED_TOTAL_ASSETS
    assert record["Denomination"] == "USD"
    assert record["_synthetic_usd_denomination"] is True
    assert record["_denomination_token"]["address"] is None
    assert record["_nav_source"] == "spiko_ustbl_oracle_latestRoundData"
    assert record["_deposit_closed_reason"] == SPIKO_PERMISSIONED_FLOW_REASON
    curator = identify_curator(1, "USTBL", "Spiko US T-Bills Money Market Fund", USTBL_TOKEN_ADDRESS, "spiko")
    assert curator == "spiko-curator"
    assert is_protocol_curator(curator)

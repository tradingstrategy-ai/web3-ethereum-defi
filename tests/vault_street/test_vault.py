"""Test the Vault Street primeUSD adapter against Ethereum mainnet."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault_street.constants import PRIME_USD_ADDRESS, PRIME_USD_FIRST_SEEN_AT, PRIME_USD_FIRST_SEEN_AT_BLOCK
from eth_defi.vault_street.historical import VaultStreetHistoricalReader
from eth_defi.vault_street.vault import VAULT_STREET_PERMISSIONED_FLOW_REASON, VaultStreetVault

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

#: Fixed post-launch block with known supply and published NAV/share.
PRIME_USD_TEST_BLOCK = 25_532_433
PRIME_USD_EXPECTED_TOTAL_SUPPLY = Decimal("5237057.988099")
PRIME_USD_EXPECTED_SHARE_PRICE = Decimal("1.00241049")
PRIME_USD_EXPECTED_TOTAL_ASSETS = Decimal("5249681.86400873275851")


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum mainnet at a deterministic primeUSD block."""

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=PRIME_USD_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 connection to the fixed Ethereum fork."""

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_vault_street_hardcoded_protocol() -> None:
    """Classify primeUSD through its hardcoded non-ERC-4626 address."""

    features = HARDCODED_PROTOCOLS[PRIME_USD_ADDRESS]

    assert features == {ERC4626Feature.vault_street_like}
    assert get_vault_protocol_name(features) == "Vault Street"


@flaky.flaky
def test_vault_street_autodetect_live_prime_usd(web3: Web3) -> None:
    """Autodetect primeUSD and read its supply, NAV and access status."""

    vault = create_vault_instance_autodetect(web3, vault_address=PRIME_USD_ADDRESS)

    assert isinstance(vault, VaultStreetVault)
    assert vault.features == {ERC4626Feature.vault_street_like}
    assert vault.get_protocol_name() == "Vault Street"
    assert vault.name == "Vault Street Prime USD"
    assert vault.symbol == "primeUSD"
    assert vault.denomination_token.symbol == "USDC"
    assert vault.fetch_total_supply(PRIME_USD_TEST_BLOCK) == PRIME_USD_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_share_price(PRIME_USD_TEST_BLOCK) == PRIME_USD_EXPECTED_SHARE_PRICE
    assert vault.fetch_total_assets(PRIME_USD_TEST_BLOCK) == PRIME_USD_EXPECTED_TOTAL_ASSETS
    assert vault.fetch_nav(PRIME_USD_TEST_BLOCK) == PRIME_USD_EXPECTED_TOTAL_ASSETS
    assert vault.fetch_info()["nav_source"] == "vault_street_price_storage_getPrice"
    assert vault.fetch_deposit_closed_reason() == VAULT_STREET_PERMISSIONED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == VAULT_STREET_PERMISSIONED_FLOW_REASON

    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()

    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


@flaky.flaky
def test_vault_street_historical_reader_and_scan_record(web3: Web3) -> None:
    """Read primeUSD historical data and create a shared scan record."""

    vault = create_vault_instance_autodetect(web3, vault_address=PRIME_USD_ADDRESS)
    reader = vault.get_historical_reader(stateful=False)

    assert isinstance(reader, VaultStreetHistoricalReader)

    call_results = [call.call_as_result(web3, block_identifier=PRIME_USD_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(PRIME_USD_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(PRIME_USD_TEST_BLOCK, timestamp, call_results)

    assert read.share_price == PRIME_USD_EXPECTED_SHARE_PRICE
    assert read.total_supply == PRIME_USD_EXPECTED_TOTAL_SUPPLY
    assert read.total_assets == PRIME_USD_EXPECTED_TOTAL_ASSETS
    assert read.errors is None

    detection = ERC4262VaultDetection(
        chain=web3.eth.chain_id,
        address=PRIME_USD_ADDRESS,
        first_seen_at_block=PRIME_USD_FIRST_SEEN_AT_BLOCK,
        first_seen_at=PRIME_USD_FIRST_SEEN_AT,
        features={ERC4626Feature.vault_street_like},
        updated_at=PRIME_USD_FIRST_SEEN_AT,
        deposit_count=0,
        redeem_count=0,
    )
    record = create_vault_scan_record(web3, detection, block_identifier=PRIME_USD_TEST_BLOCK, token_cache={})

    assert record["Protocol"] == "Vault Street"
    assert record["Denomination"] == "USDC"
    assert record["NAV"] == PRIME_USD_EXPECTED_TOTAL_ASSETS
    assert record["Shares"] == PRIME_USD_EXPECTED_TOTAL_SUPPLY
    assert record["Features"] == "vault_street_like"
    assert record["_nav_source"] == "vault_street_price_storage_getPrice"
    assert record["_deposit_closed_reason"] == VAULT_STREET_PERMISSIONED_FLOW_REASON


@flaky.flaky
def test_vault_street_historical_reader_updates_state(web3: Web3) -> None:
    """Persist primeUSD reader state after a successful historical sample."""

    vault = create_vault_instance_autodetect(web3, vault_address=PRIME_USD_ADDRESS)
    reader = vault.get_historical_reader(stateful=True)

    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(PRIME_USD_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    call_results = [call.call_as_result(web3, block_identifier=PRIME_USD_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    for call_result in call_results:
        call_result.timestamp = timestamp
        call_result.state = reader.reader_state

    reader.process_result(PRIME_USD_TEST_BLOCK, timestamp, call_results)

    assert reader.reader_state.last_block == PRIME_USD_TEST_BLOCK
    assert reader.reader_state.entry_count == 1
    assert reader.reader_state.last_tvl == PRIME_USD_EXPECTED_TOTAL_ASSETS
    assert reader.reader_state.exchange_rate == 1

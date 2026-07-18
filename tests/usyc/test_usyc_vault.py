"""Test Circle USYC against a deterministic Ethereum mainnet fork."""

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
from eth_defi.tokenised_fund.usyc.constants import USYC_FIRST_SEEN_AT, USYC_FIRST_SEEN_AT_BLOCK, USYC_TOKEN_ADDRESS
from eth_defi.tokenised_fund.usyc.historical import USYCHistoricalReader
from eth_defi.tokenised_fund.usyc.vault import USYC_DEPOSIT_FEE, USYC_PERFORMANCE_FEE, USYC_PERMISSIONED_FLOW_REASON, USYC_WITHDRAW_FEE, USYCVault
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

USYC_TEST_BLOCK = 25_550_000
USYC_EXPECTED_TOTAL_SUPPLY = Decimal("81015541.068726")
USYC_EXPECTED_SHARE_PRICE = Decimal("1.131201084907472711")
USYC_EXPECTED_TOTAL_ASSETS = USYC_EXPECTED_TOTAL_SUPPLY * USYC_EXPECTED_SHARE_PRICE


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at a fixed USYC oracle observation."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=USYC_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create Web3 connected to the deterministic fork."""
    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_usyc_hardcoded_protocol() -> None:
    """Classify USYC through its chain-specific hardcoded fund address."""
    features = HARDCODED_PROTOCOLS[USYC_TOKEN_ADDRESS]
    assert features == {ERC4626Feature.usyc_like}
    assert get_vault_protocol_name(features) == "Circle USYC"
    assert _get_hardcoded_protocol_features(USYC_TOKEN_ADDRESS, chain_id=1) == features
    assert _get_hardcoded_protocol_features(USYC_TOKEN_ADDRESS, chain_id=42161) is None


@flaky.flaky
def test_usyc_adapter_and_historical_reader(web3: Web3) -> None:
    """Read USYC supply, oracle NAV and permissioned-flow metadata."""
    vault = create_vault_instance_autodetect(web3, vault_address=USYC_TOKEN_ADDRESS)
    assert isinstance(vault, USYCVault)
    assert vault.features == {ERC4626Feature.usyc_like}
    assert vault.name == "US Yield Coin"
    assert vault.symbol == "USYC"
    assert vault.denomination_token.symbol == "USDC"
    assert vault.fetch_total_supply(USYC_TEST_BLOCK) == USYC_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_share_price(USYC_TEST_BLOCK) == USYC_EXPECTED_SHARE_PRICE
    assert vault.fetch_total_assets(USYC_TEST_BLOCK) == USYC_EXPECTED_TOTAL_ASSETS
    assert vault.fetch_deposit_closed_reason() == USYC_PERMISSIONED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == USYC_PERMISSIONED_FLOW_REASON
    assert vault.get_fee_data().fee_mode is VaultFeeMode.externalised
    assert vault.get_fee_data().performance == USYC_PERFORMANCE_FEE
    assert vault.get_fee_data().deposit == USYC_DEPOSIT_FEE
    assert vault.get_fee_data().withdraw == USYC_WITHDRAW_FEE
    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()

    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, USYCHistoricalReader)
    results = [call.call_as_result(web3, block_identifier=USYC_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(USYC_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(USYC_TEST_BLOCK, timestamp, results)
    assert read.share_price == USYC_EXPECTED_SHARE_PRICE
    assert read.total_supply == USYC_EXPECTED_TOTAL_SUPPLY
    assert read.total_assets == USYC_EXPECTED_TOTAL_ASSETS
    assert read.errors is None


@flaky.flaky
def test_usyc_scan_record(web3: Web3) -> None:
    """Export USYC data through the shared vault scan record."""
    detection = ERC4262VaultDetection(chain=1, address=USYC_TOKEN_ADDRESS, first_seen_at_block=USYC_FIRST_SEEN_AT_BLOCK, first_seen_at=USYC_FIRST_SEEN_AT, features={ERC4626Feature.usyc_like}, updated_at=USYC_FIRST_SEEN_AT, deposit_count=0, redeem_count=0)
    record = create_vault_scan_record(web3, detection, block_identifier=USYC_TEST_BLOCK, token_cache={})
    assert record["Protocol"] == "Circle USYC"
    assert record["NAV"] == USYC_EXPECTED_TOTAL_ASSETS
    assert record["_nav_source"] == "circle_usyc_oracle_latestRoundData"
    assert record["_deposit_closed_reason"] == USYC_PERMISSIONED_FLOW_REASON

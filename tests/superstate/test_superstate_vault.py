"""Test read-only Superstate USTB tokenised-fund support."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance_autodetect  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.superstate.constants import USTB_ETHEREUM_ADDRESS, USTB_ETHEREUM_CONTINUOUS_PRICE_ORACLE, USTB_ETHEREUM_FIRST_SEEN_AT, USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK
from eth_defi.tokenised_fund.superstate.historical import SuperstateVaultHistoricalReader
from eth_defi.tokenised_fund.superstate.vault import SUPERSTATE_RESTRICTED_FLOW_REASON, SuperstateVault
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

#: Fixed archive block measured when the integration was added.
USTB_TEST_BLOCK = 25_553_180
USTB_EXPECTED_RAW_TOTAL_SUPPLY = 58_961_231_154_799
USTB_EXPECTED_TOTAL_SUPPLY = Decimal("58961231.154799")
USTB_EXPECTED_NAV_PER_SHARE = Decimal("11.148734")
USTB_EXPECTED_TOTAL_ASSETS = Decimal("657343082.457366874466")
USTB_EXPECTED_DECIMALS = 6


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at the reviewed USTB test block."""

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=USTB_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a deterministic fork connection.

    :param anvil_ethereum_fork:
        Running fixed-block fork.
    :return:
        Fork Web3 instance.
    """

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_superstate_hardcoded_classification_is_chain_aware() -> None:
    """Classify USTB only on its reviewed Ethereum deployment."""

    assert _get_hardcoded_protocol_features(USTB_ETHEREUM_ADDRESS, chain_id=1) == {ERC4626Feature.superstate_like}
    assert _get_hardcoded_protocol_features(USTB_ETHEREUM_ADDRESS, chain_id=8453) is None


@flaky.flaky
def test_superstate_ustb_live_metadata_and_restricted_flows(web3: Web3) -> None:
    """Read USTB metadata and keep untested public flows unavailable."""

    vault = create_vault_instance_autodetect(web3, vault_address=USTB_ETHEREUM_ADDRESS)

    assert isinstance(vault, SuperstateVault)
    assert vault.features == {ERC4626Feature.superstate_like}
    assert vault.get_protocol_name() == "Superstate"
    assert vault.share_token.name == "Superstate Short Duration US Government Securities Fund"
    assert vault.share_token.symbol == "USTB"
    assert vault.share_token.decimals == USTB_EXPECTED_DECIMALS
    assert vault.fetch_denomination_token_address() is None
    assert vault.fetch_denomination_token() is None
    assert vault.denomination_token is None
    assert vault.fetch_info()["nav_oracle"] == USTB_ETHEREUM_CONTINUOUS_PRICE_ORACLE
    assert vault.fetch_info()["nav_estimated"] is False
    assert vault.get_flags() == {VaultFlag.tokenised_fund}
    assert vault.fetch_deposit_closed_reason() == SUPERSTATE_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == SUPERSTATE_RESTRICTED_FLOW_REASON
    assert vault.get_deposit_manager_capability() is None
    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()
    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


@flaky.flaky
def test_superstate_ustb_live_supply_nav_and_history(web3: Web3) -> None:
    """Read USTB supply and archive-block continuous NAV/share."""

    vault = create_vault_instance_autodetect(web3, vault_address=USTB_ETHEREUM_ADDRESS)
    raw_supply = vault.share_token.contract.functions.totalSupply().call(block_identifier=USTB_TEST_BLOCK)
    assert raw_supply == USTB_EXPECTED_RAW_TOTAL_SUPPLY
    assert vault.fetch_total_supply(USTB_TEST_BLOCK) == USTB_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_share_price(USTB_TEST_BLOCK) == USTB_EXPECTED_NAV_PER_SHARE
    assert vault.fetch_total_assets(USTB_TEST_BLOCK) == USTB_EXPECTED_TOTAL_ASSETS
    assert vault.fetch_nav(USTB_TEST_BLOCK) == USTB_EXPECTED_TOTAL_ASSETS

    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, SuperstateVaultHistoricalReader)
    call_results = [call.call_as_result(web3, block_identifier=USTB_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(USTB_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(USTB_TEST_BLOCK, timestamp, call_results)
    assert read.total_supply == USTB_EXPECTED_TOTAL_SUPPLY
    assert read.share_price == USTB_EXPECTED_NAV_PER_SHARE
    assert read.total_assets == USTB_EXPECTED_TOTAL_ASSETS
    assert read.errors is None


def test_superstate_lead_constants_are_consistent() -> None:
    """Keep the hardcoded lead registry internally consistent."""

    assert USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK > 0
    assert USTB_ETHEREUM_FIRST_SEEN_AT.tzinfo is None

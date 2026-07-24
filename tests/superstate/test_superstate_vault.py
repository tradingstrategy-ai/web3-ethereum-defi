"""Test read-only Superstate USTB tokenised-fund support."""

# Test helpers mirror discovery callback signatures.
# ruff: noqa: ARG001, FBT001, FBT002, PLR6301

import datetime
import os
from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626 import discovery_base as discovery_base_module
from eth_defi.erc_4626.classification import VaultFeatureProbe, _get_hardcoded_protocol_features, create_vault_instance_autodetect  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES, LeadScanReport, VaultDiscoveryBase
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.superstate.constants import SUPERSTATE_ETHEREUM_CHAIN_ID, SUPERSTATE_HARDCODED_LEADS, USTB_ETHEREUM_ADDRESS, USTB_ETHEREUM_CONTINUOUS_PRICE_ORACLE, USTB_ETHEREUM_FIRST_SEEN_AT, USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK
from eth_defi.tokenised_fund.superstate.historical import SuperstateVaultHistoricalReader, SuperstateVaultReaderState
from eth_defi.tokenised_fund.superstate.vault import SUPERSTATE_RESTRICTED_FLOW_REASON, SuperstateVault
from eth_defi.tokenised_fund.vault import TokenisedFundDepositManager
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Fixed archive block measured when the integration was added.
USTB_TEST_BLOCK = 25_553_180
USTB_EXPECTED_RAW_TOTAL_SUPPLY = 58_961_231_154_799
USTB_EXPECTED_TOTAL_SUPPLY = Decimal("58961231.154799")
USTB_EXPECTED_NAV_PER_SHARE = Decimal("11.148734")
USTB_EXPECTED_TOTAL_ASSETS = Decimal("657343082.457366874466")
USTB_EXPECTED_DECIMALS = 6


class DummySuperstateDiscovery(VaultDiscoveryBase):
    """Minimal discovery backend for hardcoded Superstate leads."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=SUPERSTATE_ETHEREUM_CHAIN_ID))
    web3factory = object()

    def fetch_leads(self, _start_block: int, _end_block: int, _display_progress: bool = True) -> LeadScanReport:
        """Return no event-derived leads.

        :return:
            Empty discovery report so hardcoded injection is isolated.
        """

        return LeadScanReport()


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at the reviewed USTB test block."""

    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run Superstate integration tests")
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


def test_superstate_hardcoded_lead_is_added_to_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject USTB without relying on ERC-4626 flow events."""

    def fake_probe_vaults(chain: int, web3factory: object, addresses: list[str], **kwargs: object) -> Iterator[VaultFeatureProbe]:
        """Yield the expected hardcoded Superstate feature."""

        assert chain == SUPERSTATE_ETHEREUM_CHAIN_ID
        assert web3factory is DummySuperstateDiscovery.web3factory
        assert addresses == [USTB_ETHEREUM_ADDRESS]
        yield VaultFeatureProbe(address=USTB_ETHEREUM_ADDRESS, features={ERC4626Feature.superstate_like})

    assert ("Superstate", SUPERSTATE_HARDCODED_LEADS) in DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
    monkeypatch.setattr(discovery_base_module, "probe_vaults", fake_probe_vaults)
    report = DummySuperstateDiscovery(max_workers=1).scan_vaults(
        0,
        USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK,
        display_progress=False,
        hardcoded_lead_sources=(("Superstate", SUPERSTATE_HARDCODED_LEADS),),
    )
    assert report.new_leads == 1
    assert report.detections[USTB_ETHEREUM_ADDRESS].features == {ERC4626Feature.superstate_like}


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
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {"can_deposit": False, "can_redeem": False}
    assert isinstance(vault.get_deposit_manager(), TokenisedFundDepositManager)
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

    reader = vault.get_historical_reader(stateful=True)
    assert isinstance(reader, SuperstateVaultHistoricalReader)
    assert isinstance(reader.reader_state, SuperstateVaultReaderState)
    call_results = [call.call_as_result(web3, block_identifier=USTB_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(USTB_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    for result in call_results:
        result.timestamp = timestamp
    read = reader.process_result(USTB_TEST_BLOCK, timestamp, call_results)
    assert read.total_supply == USTB_EXPECTED_TOTAL_SUPPLY
    assert read.share_price == USTB_EXPECTED_NAV_PER_SHARE
    assert read.total_assets == USTB_EXPECTED_TOTAL_ASSETS
    assert read.errors is None
    assert reader.reader_state.exchange_rate == Decimal(1)
    assert reader.reader_state.last_block == USTB_TEST_BLOCK
    assert reader.reader_state.last_tvl == USTB_EXPECTED_TOTAL_ASSETS
    assert reader.reader_state.last_share_price == USTB_EXPECTED_NAV_PER_SHARE


def test_superstate_lead_constants_are_consistent() -> None:
    """Keep the hardcoded lead registry internally consistent."""

    assert USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK > 0
    assert USTB_ETHEREUM_FIRST_SEEN_AT.tzinfo is None
    assert SUPERSTATE_HARDCODED_LEADS == ((SUPERSTATE_ETHEREUM_CHAIN_ID, USTB_ETHEREUM_ADDRESS, USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK, USTB_ETHEREUM_FIRST_SEEN_AT),)

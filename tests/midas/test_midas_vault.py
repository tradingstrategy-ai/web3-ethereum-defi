"""Test Midas vault tracking against live Ethereum mToken products."""

import datetime
import json
import os
from collections.abc import Iterable
from decimal import Decimal
from types import SimpleNamespace

import flaky
import hypersync
import pandas as pd
import pytest
from web3 import Web3

from eth_defi.erc_4626 import discovery_base as discovery_base_module
from eth_defi.erc_4626.classification import VaultFeatureProbe, create_vault_instance_autodetect, identify_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import LeadScanReport, VaultDiscoveryBase
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.hypersync_timestamp import get_block_timestamps_using_hypersync
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.session import ThrottledHypersyncClient, create_throttled_hypersync_client
from eth_defi.midas.constants import MIDAS_MBASIS_ETHEREUM, MIDAS_MTBILL_ETHEREUM, MIDAS_PRODUCTS
from eth_defi.midas.historical import MidasVaultHistoricalReader, MidasVaultReaderState
from eth_defi.midas.registry import (
    MIDAS_ADDRESSES_PER_NETWORK,
    MIDAS_CHAIN_IDS,
    MIDAS_PRODUCT_DEPLOYMENTS,
    MIDAS_PRODUCT_SCAN_EXCLUSIONS,
    MIDAS_REGISTRY_SOURCE_COMMIT,
    MIDAS_REGISTRY_SOURCE_URL,
    MIDAS_SANCTION_LIST_CONTRACTS,
    MIDAS_USTB_CONTRACTS,
    iter_midas_registry_products,
)
from eth_defi.midas.vault import MIDAS_BESPOKE_FLOW_REASON, MIDAS_NAV_SOURCE, MidasVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.top_vaults_json import validate_strict_json_serialisable
from eth_defi.vault.vaultdb import VaultDatabase

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")
ETHEREUM_CHAIN_ID = 1
GIT_COMMIT_SHA_LENGTH = 40

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

#: Fixed block used for deterministic Midas assertions.
MIDAS_TEST_BLOCK = 25_495_779

MTBILL_EXPECTED_RAW_TOTAL_SUPPLY = 49_828_005_665_862_591_900_646_279
MTBILL_EXPECTED_TOTAL_SUPPLY = Decimal("49828005.665862591900646279")
MTBILL_EXPECTED_SHARE_PRICE = Decimal("1.06563587")
MTBILL_EXPECTED_TOTAL_ASSETS = Decimal("53098510.16810641242050015108")
MTBILL_EXPECTED_WITHDRAW_FEE = 0.0007
MTBILL_EXPECTED_DECIMALS = 18

MBASIS_EXPECTED_TOTAL_SUPPLY = Decimal("105365.233087777728658011")
MBASIS_EXPECTED_SHARE_PRICE = Decimal("1.19665044")
MBASIS_EXPECTED_WITHDRAW_FEE = 0.03
JIV_EXPECTED_FALLBACK_SHARE_PRICE = Decimal("1")
MIDAS_EXPECTED_SCANNED_DEPLOYMENT_COUNT = 91
MIDAS_EXPECTED_ADAPTER_PRODUCT_COUNT = 90
MIDAS_MINIMUM_SUPPORTED_PRODUCT_COUNT = 2
MIDAS_JIV_ETHEREUM = next(product for product in MIDAS_PRODUCTS.values() if product.chain_id == ETHEREUM_CHAIN_ID and product.symbol == "JIV")

MIDAS_MTBILL_HISTORY_BLOCKS = [
    25_474_179,
    25_481_379,
    25_488_579,
    25_495_779,
]
MIDAS_MTBILL_HISTORY_SHARE_PRICES = [
    Decimal("1.06508284"),
    Decimal("1.06519674"),
    Decimal("1.06519674"),
    Decimal("1.06563587"),
]
MIDAS_MTBILL_HISTORY_TOTAL_ASSETS = [
    Decimal("52857331.36940752968230447663"),
    Decimal("52914748.74476991251074978688"),
    Decimal("53076696.69748991251074978643"),
    Decimal("53098510.16810641242050015108"),
]
MIDAS_MTBILL_HISTORY_TOTAL_SUPPLIES = [
    Decimal("49627436.838065600307957714"),
    Decimal("49676033.316408678185355493"),
    Decimal("49828069.035857087312105167"),
    Decimal("49828005.665862591900646279"),
]


def _create_hypersync_client() -> ThrottledHypersyncClient:
    """Create an Ethereum HyperSync client for live timestamp checks."""

    return create_throttled_hypersync_client(
        hypersync.ClientConfig(
            url=get_hypersync_server(ETHEREUM_CHAIN_ID),
            bearer_token=HYPERSYNC_API_KEY,
        ),
        concurrency=1,
    )


class DummyMidasDiscovery(VaultDiscoveryBase):
    """Minimal discovery backend for testing hardcoded Midas lead injection."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    web3factory = object()

    def fetch_leads(  # noqa: PLR6301
        self,
        _start_block: int,
        _end_block: int,
        _display_progress: bool = True,  # noqa: FBT001, FBT002
    ) -> LeadScanReport:
        """Return an empty discovery report."""

        return LeadScanReport()


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum mainnet at a fixed block for Midas integration tests."""

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=MIDAS_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 connection to the fixed-block Anvil fork."""

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_midas_hardcoded_leads_are_added_to_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add Midas mTokens as scanner leads without ERC-4626 flow events."""

    expected_mainnet_products = {product.token: product for product in MIDAS_PRODUCTS.values() if product.chain_id == ETHEREUM_CHAIN_ID}
    expected_end_block = max(product.first_seen_at_block for product in expected_mainnet_products.values())

    def fake_probe_vaults(
        chain: int,
        web3factory: object,
        addresses: list[str],
        *,
        block_identifier: int,
        max_workers: int,
        progress_bar_desc: str | None,
    ) -> Iterable[VaultFeatureProbe]:
        """Return Midas features for the hardcoded Midas lead addresses."""

        assert chain == 1
        assert web3factory is DummyMidasDiscovery.web3factory
        assert block_identifier == expected_end_block
        assert max_workers == 1
        assert progress_bar_desc is None
        assert MIDAS_MTBILL_ETHEREUM.token in addresses
        assert MIDAS_MBASIS_ETHEREUM.token in addresses

        for address in addresses:
            yield VaultFeatureProbe(
                address=address,
                features={ERC4626Feature.midas_like},
            )

    monkeypatch.setattr(discovery_base_module, "probe_vaults", fake_probe_vaults)
    monkeypatch.setattr(discovery_base_module, "ODA_FACT_HARDCODED_LEADS", ())

    discover = DummyMidasDiscovery(max_workers=1)
    report = discover.scan_vaults(
        start_block=0,
        end_block=expected_end_block,
        display_progress=False,
    )

    assert report.new_leads == len(expected_mainnet_products)
    assert set(report.leads) == set(expected_mainnet_products)
    assert set(report.detections) == set(expected_mainnet_products)
    assert report.detections[MIDAS_MTBILL_ETHEREUM.token].features == {ERC4626Feature.midas_like}
    assert report.detections[MIDAS_MBASIS_ETHEREUM.token].features == {ERC4626Feature.midas_like}


def test_midas_hardcoded_classification_is_chain_aware() -> None:
    """Do not classify same-address Midas mTokens on unsupported chains."""

    failed_call = SimpleNamespace(success=False, result=b"")
    calls = {
        "EVM IS BROKEN SHIT": failed_call,
        "shareManager": failed_call,
        "getAssetCount": failed_call,
        "assetsWhitelistAddress": failed_call,
        "convertToShares": failed_call,
    }

    assert identify_vault_features(
        MIDAS_MTBILL_ETHEREUM.token,
        calls,
        debug_text="midas mainnet",
        chain_id=ETHEREUM_CHAIN_ID,
    ) == {ERC4626Feature.midas_like}

    base_chain_features = identify_vault_features(
        MIDAS_MTBILL_ETHEREUM.token,
        calls,
        debug_text="midas base",
        chain_id=8453,
    )
    assert base_chain_features == {ERC4626Feature.midas_like}

    unsupported_chain_features = identify_vault_features(
        MIDAS_MTBILL_ETHEREUM.token,
        calls,
        debug_text="midas unsupported",
        chain_id=31337,
    )
    assert ERC4626Feature.midas_like not in unsupported_chain_features


def test_midas_registry_contains_supported_mainnet_products() -> None:
    """Check the Pythonised Midas registry has the products used by the adapter."""

    assert MIDAS_REGISTRY_SOURCE_URL.endswith("midas-apps/contracts/main/config/constants/addresses.ts")
    assert len(MIDAS_REGISTRY_SOURCE_COMMIT) == GIT_COMMIT_SHA_LENGTH
    assert MIDAS_CHAIN_IDS["main"] == ETHEREUM_CHAIN_ID
    assert MIDAS_SANCTION_LIST_CONTRACTS[ETHEREUM_CHAIN_ID] == "0x40C57923924B5c5c5455c48D93317139ADDaC8fb"
    assert MIDAS_USTB_CONTRACTS[ETHEREUM_CHAIN_ID] == "0x43415eB6ff9DB7E26A15b704e7A3eDCe97d31C4e"

    mainnet = MIDAS_ADDRESSES_PER_NETWORK["main"]
    assert mainnet is not None

    mtbill = mainnet["mTBILL"]
    assert mtbill["token"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.token)
    assert mtbill["dataFeed"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.data_feed)
    assert mtbill["customFeed"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.oracle)
    assert mtbill["depositVault"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.issuance_vault)
    assert mtbill["redemptionVault"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.redemption_vault)

    mbasis = mainnet["mBASIS"]
    assert mbasis["token"] == Web3.to_checksum_address(MIDAS_MBASIS_ETHEREUM.token)
    assert mbasis["dataFeed"] == Web3.to_checksum_address(MIDAS_MBASIS_ETHEREUM.data_feed)
    assert mbasis["customFeed"] == Web3.to_checksum_address(MIDAS_MBASIS_ETHEREUM.oracle)
    assert mbasis["depositVault"] == Web3.to_checksum_address(MIDAS_MBASIS_ETHEREUM.issuance_vault)
    assert mbasis["redemptionVault"] == Web3.to_checksum_address(MIDAS_MBASIS_ETHEREUM.redemption_vault)


def test_midas_registry_iterates_scannable_products() -> None:
    """Build a scanner-friendly view of Midas products with token/datafeed pairs."""

    products = list(iter_midas_registry_products(require_historical_contracts=True))
    adapter_products = list(iter_midas_registry_products(require_historical_contracts=True, require_adapter_data=True))

    assert len(products) > MIDAS_MINIMUM_SUPPORTED_PRODUCT_COUNT
    assert len(MIDAS_PRODUCTS) == MIDAS_EXPECTED_ADAPTER_PRODUCT_COUNT
    assert len(adapter_products) == len(MIDAS_PRODUCTS)
    assert len(MIDAS_PRODUCT_DEPLOYMENTS) == MIDAS_EXPECTED_SCANNED_DEPLOYMENT_COUNT
    assert MIDAS_PRODUCT_SCAN_EXCLUSIONS["base", "mRE7"].startswith("dataFeed is deprecated")
    assert all(product.has_required_historical_contracts for product in products)
    assert all(product.has_required_adapter_data for product in adapter_products)
    assert all(product.symbol != "paymentTokens" for product in products)

    by_key = {(product.network, product.symbol): product for product in products}
    assert set(MIDAS_PRODUCT_DEPLOYMENTS).issubset(by_key)

    mtbill = by_key["main", "mTBILL"]
    assert mtbill.chain_id == ETHEREUM_CHAIN_ID
    assert mtbill.token == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.token)
    assert mtbill.data_feed == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.data_feed)
    assert mtbill.custom_feed == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.oracle)
    assert mtbill.deposit_vault == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.issuance_vault)
    assert mtbill.redemption_vault == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.redemption_vault)
    assert mtbill.first_seen_at_block == MIDAS_MTBILL_ETHEREUM.first_seen_at_block
    assert mtbill.first_seen_at == MIDAS_MTBILL_ETHEREUM.first_seen_at
    assert mtbill.rpc_env_var == "JSON_RPC_ETHEREUM"

    mbasis = by_key["main", "mBASIS"]
    assert mbasis.token == Web3.to_checksum_address(MIDAS_MBASIS_ETHEREUM.token)
    assert mbasis.data_feed == Web3.to_checksum_address(MIDAS_MBASIS_ETHEREUM.data_feed)
    assert mbasis.custom_feed == Web3.to_checksum_address(MIDAS_MBASIS_ETHEREUM.oracle)
    assert mbasis.first_seen_at_block == MIDAS_MBASIS_ETHEREUM.first_seen_at_block
    assert mbasis.first_seen_at == MIDAS_MBASIS_ETHEREUM.first_seen_at


@flaky.flaky
def test_midas_anvil_forked_mtbill_properties(web3: Web3) -> None:
    """Read Midas mTBILL adapter properties on a fixed Anvil Ethereum fork."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=MIDAS_MTBILL_ETHEREUM.token,
    )

    assert isinstance(vault, MidasVault)
    assert vault.chain_id == ETHEREUM_CHAIN_ID
    assert vault.address == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.token)
    assert vault.vault_address == vault.address
    assert vault.name == "Midas US Treasury Bill Token"
    assert vault.symbol == "mTBILL"
    assert vault.description == MIDAS_MTBILL_ETHEREUM.product_name
    assert vault.manager_name == "Midas"
    assert vault.get_link() == "https://midas.app/products"
    assert vault.has_block_range_event_support() is False
    assert vault.has_deposit_distribution_to_all_positions() is False
    assert vault.fetch_portfolio(universe=None).spot_erc20 == {}

    info = vault.fetch_info()
    assert info["chain_id"] == ETHEREUM_CHAIN_ID
    assert info["token"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.token)
    assert info["data_feed"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.data_feed)
    assert info["oracle"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.oracle)
    assert info["issuance_vault"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.issuance_vault)
    assert info["redemption_vault"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.redemption_vault)
    assert info["synthetic_usd_denomination"] is True
    assert info["nav_source"] == MIDAS_NAV_SOURCE
    assert info["nav_estimated"] is False


@flaky.flaky
def test_midas_autodetect_live_mtbill(web3: Web3) -> None:
    """Autodetect the live Midas mTBILL product."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=MIDAS_MTBILL_ETHEREUM.token,
    )

    assert isinstance(vault, MidasVault)
    assert vault.features == {ERC4626Feature.midas_like}
    assert vault.get_protocol_name() == "Midas"
    assert vault.address == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.token)
    assert vault.share_token.name == "Midas US Treasury Bill Token"
    assert vault.share_token.symbol == "mTBILL"
    assert vault.share_token.decimals == MTBILL_EXPECTED_DECIMALS
    assert vault.fetch_denomination_token_address() is None
    assert vault.fetch_denomination_token() is None
    assert vault.denomination_token is None


@flaky.flaky
def test_midas_live_supply_nav_and_unsupported_actions(web3: Web3) -> None:
    """Read live Midas mTBILL supply/NAV and ensure active flows stay unsupported."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=MIDAS_MTBILL_ETHEREUM.token,
    )

    raw_supply = vault.share_token.contract.functions.totalSupply().call(block_identifier=MIDAS_TEST_BLOCK)
    assert raw_supply == MTBILL_EXPECTED_RAW_TOTAL_SUPPLY

    assert vault.fetch_share_price(MIDAS_TEST_BLOCK) == MTBILL_EXPECTED_SHARE_PRICE
    assert vault.fetch_total_supply(MIDAS_TEST_BLOCK) == MTBILL_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_total_assets(MIDAS_TEST_BLOCK) == MTBILL_EXPECTED_TOTAL_ASSETS
    assert vault.fetch_nav(MIDAS_TEST_BLOCK) == MTBILL_EXPECTED_TOTAL_ASSETS
    assert vault.get_fee_data().management is None
    assert vault.get_fee_data().performance is None
    assert vault.get_fee_data().deposit == 0
    assert vault.get_fee_data().withdraw == MTBILL_EXPECTED_WITHDRAW_FEE
    assert vault.fetch_info()["denomination_token"] is None
    assert vault.fetch_scan_record_extra_data()["Denomination"] == "USD"
    assert vault.fetch_scan_record_extra_data()["_denomination_token"]["symbol"] == "USD"
    assert vault.fetch_scan_record_extra_data()["_denomination_token"]["address"] is None
    assert vault.fetch_info()["nav_source"] == MIDAS_NAV_SOURCE
    assert vault.fetch_info()["nav_estimated"] is False

    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()

    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


@flaky.flaky
def test_midas_live_mbasis_uses_product_metadata(web3: Web3) -> None:
    """Read mBASIS through the same Midas adapter."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=MIDAS_MBASIS_ETHEREUM.token,
    )

    assert isinstance(vault, MidasVault)
    assert vault.share_token.symbol == "mBASIS"
    assert vault.fetch_share_price(MIDAS_TEST_BLOCK) == MBASIS_EXPECTED_SHARE_PRICE
    assert vault.fetch_total_supply(MIDAS_TEST_BLOCK) == MBASIS_EXPECTED_TOTAL_SUPPLY
    assert vault.get_fee_data().withdraw == MBASIS_EXPECTED_WITHDRAW_FEE


@flaky.flaky
def test_midas_anvil_forked_jiv_uses_custom_feed_fallback(web3: Web3) -> None:
    """Fall back to ``customFeed`` when a Midas datafeed is unhealthy."""

    vault = MidasVault(
        web3,
        VaultSpec(
            chain_id=ETHEREUM_CHAIN_ID,
            vault_address=MIDAS_JIV_ETHEREUM.token,
        ),
        default_block_identifier=MIDAS_TEST_BLOCK,
    )

    assert vault.custom_feed_contract is not None
    with pytest.raises(ValueError):
        vault.data_feed_contract.functions.getDataInBase18().call(block_identifier=MIDAS_TEST_BLOCK)

    assert vault.fetch_share_price(MIDAS_TEST_BLOCK) == JIV_EXPECTED_FALLBACK_SHARE_PRICE


@flaky.flaky
def test_midas_historical_reader_live_mtbill(web3: Web3) -> None:
    """Read a historical mTBILL sample through the Midas historical reader."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=MIDAS_MTBILL_ETHEREUM.token,
    )
    reader = vault.get_historical_reader(stateful=False)

    assert isinstance(reader, MidasVaultHistoricalReader)

    call_results = [
        call.call_as_result(
            web3,
            block_identifier=MIDAS_TEST_BLOCK,
            ignore_error=True,
        )
        for call in reader.construct_multicalls()
    ]
    timestamp = datetime.datetime.fromtimestamp(
        web3.eth.get_block(MIDAS_TEST_BLOCK)["timestamp"],
        tz=datetime.UTC,
    ).replace(tzinfo=None)
    read = reader.process_result(
        block_number=MIDAS_TEST_BLOCK,
        timestamp=timestamp,
        call_results=call_results,
    )

    assert read.block_number == MIDAS_TEST_BLOCK
    assert read.share_price == MTBILL_EXPECTED_SHARE_PRICE
    assert read.total_supply == MTBILL_EXPECTED_TOTAL_SUPPLY
    assert read.total_assets == MTBILL_EXPECTED_TOTAL_ASSETS
    assert read.performance_fee is None
    assert read.management_fee is None
    assert read.errors is None


@flaky.flaky
def test_midas_historical_reader_updates_state(web3: Web3) -> None:
    """Persist Midas reader state after a successful historical sample."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=MIDAS_MTBILL_ETHEREUM.token,
    )
    reader = vault.get_historical_reader(stateful=True)
    assert isinstance(reader, MidasVaultHistoricalReader)
    assert isinstance(reader.reader_state, MidasVaultReaderState)

    timestamp = datetime.datetime.fromtimestamp(
        web3.eth.get_block(MIDAS_TEST_BLOCK)["timestamp"],
        tz=datetime.UTC,
    ).replace(tzinfo=None)
    call_results = [
        call.call_as_result(
            web3,
            block_identifier=MIDAS_TEST_BLOCK,
            ignore_error=True,
        )
        for call in reader.construct_multicalls()
    ]
    for call_result in call_results:
        call_result.timestamp = timestamp
        call_result.state = reader.reader_state

    reader.process_result(
        block_number=MIDAS_TEST_BLOCK,
        timestamp=timestamp,
        call_results=call_results,
    )

    assert reader.reader_state.last_block == MIDAS_TEST_BLOCK
    assert reader.reader_state.entry_count == 1
    assert reader.reader_state.last_tvl == MTBILL_EXPECTED_TOTAL_ASSETS
    assert reader.reader_state.exchange_rate == 1


@flaky.flaky
def test_midas_scan_record_live_mtbill(web3: Web3) -> None:
    """Create the shared vault scan record for Midas mTBILL."""

    detection = ERC4262VaultDetection(
        chain=web3.eth.chain_id,
        address=MIDAS_MTBILL_ETHEREUM.token,
        first_seen_at_block=MIDAS_MTBILL_ETHEREUM.first_seen_at_block,
        first_seen_at=MIDAS_MTBILL_ETHEREUM.first_seen_at,
        features={ERC4626Feature.midas_like},
        updated_at=MIDAS_MTBILL_ETHEREUM.first_seen_at,
        deposit_count=0,
        redeem_count=0,
    )

    record = create_vault_scan_record(
        web3,
        detection,
        block_identifier=MIDAS_TEST_BLOCK,
        token_cache={},
    )

    assert record["Symbol"] == "mTBILL"
    assert record["Name"] == "Midas US Treasury Bill Token"
    assert record["Protocol"] == "Midas"
    assert record["Denomination"] == "USD"
    assert record["Share token"] == "mTBILL"
    assert record["NAV"] == MTBILL_EXPECTED_TOTAL_ASSETS
    assert record["Mgmt fee"] is None
    assert record["Perf fee"] is None
    assert record["Deposit fee"] == 0
    assert record["Withdraw fee"] == MTBILL_EXPECTED_WITHDRAW_FEE
    assert record["Shares"] == MTBILL_EXPECTED_TOTAL_SUPPLY
    assert record["Features"] == "midas_like"
    assert record["_detection_data"] == detection
    assert record["_denomination_token"]["symbol"] == "USD"
    assert record["_denomination_token"]["address"] is None
    assert record["_share_token"]["symbol"] == "mTBILL"
    assert record["_manager_name"] == "Midas"
    assert record["_deposit_closed_reason"] == MIDAS_BESPOKE_FLOW_REASON
    assert record["_redemption_closed_reason"] == MIDAS_BESPOKE_FLOW_REASON
    assert record["_nav_source"] == MIDAS_NAV_SOURCE
    assert record["_nav_estimated"] is False
    assert record["_synthetic_usd_denomination"] is True
    assert record["_midas_data_feed"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.data_feed)
    assert record["_midas_oracle"] == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.oracle)


@flaky.flaky
def test_midas_lead_detection_lifetime_metrics_json_export(monkeypatch: pytest.MonkeyPatch, web3: Web3) -> None:
    """Run mTBILL from lead detection to lifetime metrics and strict JSON."""

    def fake_probe_vaults(
        chain: int,
        web3factory: object,
        addresses: list[str],
        *,
        block_identifier: int,
        max_workers: int,
        progress_bar_desc: str | None,
    ) -> Iterable[VaultFeatureProbe]:
        """Return Midas features only for mTBILL."""

        assert chain == ETHEREUM_CHAIN_ID
        assert web3factory is DummyMidasDiscovery.web3factory
        assert addresses == [MIDAS_MTBILL_ETHEREUM.token]
        assert block_identifier == MIDAS_MTBILL_ETHEREUM.first_seen_at_block
        assert max_workers == 1
        assert progress_bar_desc is None

        yield VaultFeatureProbe(
            address=MIDAS_MTBILL_ETHEREUM.token,
            features={ERC4626Feature.midas_like},
        )

    monkeypatch.setattr(discovery_base_module, "probe_vaults", fake_probe_vaults)
    monkeypatch.setattr(
        discovery_base_module,
        "MIDAS_HARDCODED_LEADS",
        (
            (
                MIDAS_MTBILL_ETHEREUM.chain_id,
                MIDAS_MTBILL_ETHEREUM.token,
                MIDAS_MTBILL_ETHEREUM.first_seen_at_block,
                MIDAS_MTBILL_ETHEREUM.first_seen_at,
            ),
        ),
    )

    discover = DummyMidasDiscovery(max_workers=1)
    report = discover.scan_vaults(
        start_block=MIDAS_MTBILL_ETHEREUM.first_seen_at_block,
        end_block=MIDAS_MTBILL_ETHEREUM.first_seen_at_block,
        display_progress=False,
    )
    detection = report.detections[MIDAS_MTBILL_ETHEREUM.token]

    scan_record = create_vault_scan_record(
        web3,
        detection,
        block_identifier=MIDAS_TEST_BLOCK,
        token_cache={},
    )
    vault_spec = VaultSpec(chain_id=detection.chain, vault_address=detection.address)
    vault_db = VaultDatabase(rows={vault_spec: scan_record})

    timestamps = [datetime.datetime.fromtimestamp(web3.eth.get_block(block_number)["timestamp"], tz=datetime.UTC).replace(tzinfo=None) for block_number in MIDAS_MTBILL_HISTORY_BLOCKS]
    prices_df = pd.DataFrame(
        {
            "chain": [detection.chain] * len(MIDAS_MTBILL_HISTORY_BLOCKS),
            "address": [detection.address] * len(MIDAS_MTBILL_HISTORY_BLOCKS),
            "id": [f"{detection.chain}-{detection.address}"] * len(MIDAS_MTBILL_HISTORY_BLOCKS),
            "block_number": MIDAS_MTBILL_HISTORY_BLOCKS,
            "share_price": [float(value) for value in MIDAS_MTBILL_HISTORY_SHARE_PRICES],
            "total_assets": [float(value) for value in MIDAS_MTBILL_HISTORY_TOTAL_ASSETS],
            "total_supply": [float(value) for value in MIDAS_MTBILL_HISTORY_TOTAL_SUPPLIES],
            "event_count": [0] * len(MIDAS_MTBILL_HISTORY_BLOCKS),
            "vault_poll_frequency": ["large_tvl"] * len(MIDAS_MTBILL_HISTORY_BLOCKS),
        },
        index=pd.DatetimeIndex(timestamps),
    )

    returns_df = calculate_hourly_returns_for_all_vaults(prices_df)
    lifetime_data_df = calculate_lifetime_metrics(returns_df, vault_db)
    assert len(lifetime_data_df) == 1

    row = lifetime_data_df.iloc[0]
    assert row["protocol"] == "Midas"
    assert row["features"] == ["midas_like"]
    assert row["current_nav"] == pytest.approx(float(MIDAS_MTBILL_HISTORY_TOTAL_ASSETS[-1]))

    exported = export_lifetime_row(row)
    validate_strict_json_serialisable({"vaults": [exported]})
    json_payload = json.dumps({"vaults": [exported]}, allow_nan=False)
    decoded = json.loads(json_payload)

    assert decoded["vaults"][0]["protocol"] == "Midas"
    assert decoded["vaults"][0]["features"] == ["midas_like"]
    assert decoded["vaults"][0]["management_fee"] is None
    assert decoded["vaults"][0]["deposit_fee"] == 0
    assert decoded["vaults"][0]["withdraw_fee"] == MTBILL_EXPECTED_WITHDRAW_FEE
    assert decoded["vaults"][0]["denomination"] == "USD"
    assert decoded["vaults"][0]["address"] == MIDAS_MTBILL_ETHEREUM.token.lower()


@pytest.mark.skipif(HYPERSYNC_API_KEY is None, reason="HYPERSYNC_API_KEY needed to run this test")
@flaky.flaky
def test_midas_hypersync_mtbill_three_day_share_price_history() -> None:
    """Read a three-day mTBILL share-price snapshot using live HyperSync block headers.

    HyperSync supplies the historical block timestamps. Midas share prices are
    historical ``eth_call`` reads against the mToken and Midas datafeed,
    matching how the production vault price reader builds Midas rows.
    """

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM, hint="Ethereum RPC")
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=MIDAS_MTBILL_ETHEREUM.token,
    )
    reader = vault.get_historical_reader(stateful=False)
    hypersync_client = _create_hypersync_client()

    headers = {
        block_number: get_block_timestamps_using_hypersync(
            hypersync_client,
            chain_id=ETHEREUM_CHAIN_ID,
            start_block=block_number,
            end_block=block_number,
            display_progress=False,
        )[block_number]
        for block_number in MIDAS_MTBILL_HISTORY_BLOCKS
    }

    reads = []
    for block_number in MIDAS_MTBILL_HISTORY_BLOCKS:
        call_results = [
            call.call_as_result(
                web3,
                block_identifier=block_number,
                ignore_error=True,
            )
            for call in reader.construct_multicalls()
        ]
        reads.append(
            reader.process_result(
                block_number=block_number,
                timestamp=headers[block_number].timestamp_as_datetime,
                call_results=call_results,
            )
        )

    assert [read.block_number for read in reads] == MIDAS_MTBILL_HISTORY_BLOCKS
    assert reads[-1].timestamp - reads[0].timestamp >= datetime.timedelta(days=3)
    assert reads[-1].timestamp - reads[0].timestamp < datetime.timedelta(days=3, hours=1)
    assert [read.share_price for read in reads] == MIDAS_MTBILL_HISTORY_SHARE_PRICES
    assert [read.total_assets for read in reads] == MIDAS_MTBILL_HISTORY_TOTAL_ASSETS
    assert [read.total_supply for read in reads] == MIDAS_MTBILL_HISTORY_TOTAL_SUPPLIES
    assert all(read.vault.address == Web3.to_checksum_address(MIDAS_MTBILL_ETHEREUM.token) for read in reads)
    assert all(read.errors is None for read in reads)
    assert all(read.performance_fee is None for read in reads)
    assert all(read.management_fee is None for read in reads)
    assert all(read.deposits_open is False for read in reads)
    assert all(read.redemption_open is False for read in reads)
    assert reads[-1].share_price > reads[0].share_price

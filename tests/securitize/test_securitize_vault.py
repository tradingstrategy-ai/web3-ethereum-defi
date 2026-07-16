"""Test Securitize DSToken vault tracking against BlackRock BUIDL."""

import datetime
import os
from decimal import Decimal
from types import SimpleNamespace

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.classification import create_probe_calls, create_vault_instance_autodetect, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import VaultEventKind, get_securitize_dstoken_discovery_events, get_vault_event_topic_map
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.securitize.description import ACRED_ETHEREUM, ARCOIN_ETHEREUM, BCAP_ETHEREUM, BUIDL_ETHEREUM, BUIDL_I_ETHEREUM, COSX_ETHEREUM, HLSCOPE_ETHEREUM, PRTS_ETHEREUM, SCI2_ETHEREUM, SECURITIZE_PRODUCTS, SPICE_VC_ETHEREUM, STAC_ETHEREUM, VBILL_ETHEREUM
from eth_defi.securitize.historical import SecuritizeVaultHistoricalReader
from eth_defi.securitize.vault import BUIDL_ESTIMATED_NAV_PER_SHARE, BUIDL_ETHEREUM_ADDRESS, SECURITIZE_RESTRICTED_FLOW_REASON, SecuritizeVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Fixed archive block used for deterministic BUIDL assertions.
BUIDL_TEST_BLOCK = 25_450_559
BUIDL_EXPECTED_RAW_TOTAL_SUPPLY = 186_832_894_880_349
BUIDL_EXPECTED_TOTAL_SUPPLY = Decimal("186832894.880349")
BUIDL_EXPECTED_DECIMALS = 6


def test_securitize_dstoken_classification_and_issue_lead_event() -> None:
    """Identify a DSToken before the generic ERC-4626 failure branch."""

    failed_call = SimpleNamespace(success=False, result=b"")
    successful_call = SimpleNamespace(success=True, result=b"\x00" * 32)
    calls = {
        "EVM IS BROKEN SHIT": failed_call,
        "shareManager": failed_call,
        "getAssetCount": failed_call,
        "COMPLIANCE_SERVICE": successful_call,
    }

    assert identify_vault_features(
        BUIDL_ETHEREUM_ADDRESS,
        calls,
        debug_text="BUIDL",
        chain_id=1,
    ) == {ERC4626Feature.securitize_like}

    for chain_id in (1, 8453):
        probe_names = {call.func_name for call in create_probe_calls([BUIDL_ETHEREUM_ADDRESS], chain_id=chain_id)}
        assert "COMPLIANCE_SERVICE" in probe_names

    web3 = Web3()
    securitize_events = get_securitize_dstoken_discovery_events(web3)
    topic_map = get_vault_event_topic_map(web3)
    assert securitize_events[0].event_name == "Issue"
    assert topic_map[get_topic_signature_from_event(securitize_events[0])] == VaultEventKind.deposit


def test_securitize_product_registry() -> None:
    """Look up every manually-described Securitize fund by DSToken address."""

    products = (BUIDL_ETHEREUM, BUIDL_I_ETHEREUM, ACRED_ETHEREUM, VBILL_ETHEREUM, STAC_ETHEREUM, ARCOIN_ETHEREUM, SPICE_VC_ETHEREUM, HLSCOPE_ETHEREUM, BCAP_ETHEREUM, COSX_ETHEREUM, SCI2_ETHEREUM, PRTS_ETHEREUM)
    assert {SECURITIZE_PRODUCTS[product.chain_id, product.token] for product in products} == set(products)
    assert all(product.notes.startswith(product.product_name) for product in products)
    assert all(identify_curator(product.chain_id, "", product.product_name, product.token) == product.curator_slug for product in products)


def test_only_priced_dstokens_export_synthetic_usd_denomination() -> None:
    """Keep NAV-following funds out of the synthetic stablecoin classification."""

    web3 = Web3()
    for product, expected_synthetic_denomination in ((BUIDL_ETHEREUM, True), (ACRED_ETHEREUM, False), (STAC_ETHEREUM, False)):
        vault = SecuritizeVault(web3, VaultSpec(chain_id=product.chain_id, vault_address=product.token))
        scan_extra_data = vault.fetch_scan_record_extra_data()

        assert vault.fetch_info()["synthetic_usd_denomination"] is expected_synthetic_denomination
        assert scan_extra_data["_synthetic_usd_denomination"] is expected_synthetic_denomination
        assert (scan_extra_data["_denomination_token"] is not None) is expected_synthetic_denomination


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum mainnet at the fixed BUIDL test block."""

    if JSON_RPC_ETHEREUM is None:
        pytest.skip("JSON_RPC_ETHEREUM needed to run BUIDL integration tests")

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=BUIDL_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 connection to the fixed Anvil fork."""

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


@flaky.flaky
def test_buidl_autodetect_and_read_supply(web3: Web3) -> None:
    """Autodetect BUIDL and read its fixed-block supply and NAV estimate."""

    vault = create_vault_instance_autodetect(web3, vault_address=BUIDL_ETHEREUM_ADDRESS)

    assert isinstance(vault, SecuritizeVault)
    assert vault.product == BUIDL_ETHEREUM
    assert vault.features == {ERC4626Feature.securitize_like}
    assert vault.get_protocol_name() == "Securitize"
    assert vault.name == "BlackRock USD Institutional Digital Liquidity Fund"
    assert vault.symbol == "BUIDL"
    assert vault.share_token.decimals == BUIDL_EXPECTED_DECIMALS
    assert vault.share_token.contract.functions.totalSupply().call(block_identifier=BUIDL_TEST_BLOCK) == BUIDL_EXPECTED_RAW_TOTAL_SUPPLY
    assert vault.fetch_share_price(BUIDL_TEST_BLOCK) == BUIDL_ESTIMATED_NAV_PER_SHARE
    assert vault.fetch_total_supply(BUIDL_TEST_BLOCK) == BUIDL_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_total_assets(BUIDL_TEST_BLOCK) == BUIDL_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_nav(BUIDL_TEST_BLOCK) == BUIDL_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_denomination_token_address() is None
    assert vault.denomination_token is None
    assert vault.get_fee_data().management is None
    assert vault.get_flags() == {VaultFlag.tokenised_fund}
    assert "**Curator:** BlackRock / Securitize" in vault.get_notes()
    assert "BUIDL targets a USD 1 share value" in vault.get_notes()
    assert "distributed monthly as newly issued BUIDL shares" in vault.get_notes()
    assert vault.fetch_deposit_closed_reason() == SECURITIZE_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == SECURITIZE_RESTRICTED_FLOW_REASON


@flaky.flaky
def test_buidl_historical_reader(web3: Web3) -> None:
    """Read BUIDL supply through the Securitize historical reader."""

    vault = create_vault_instance_autodetect(web3, vault_address=BUIDL_ETHEREUM_ADDRESS)
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, SecuritizeVaultHistoricalReader)

    call_results = [call.call_as_result(web3, block_identifier=BUIDL_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(
        web3.eth.get_block(BUIDL_TEST_BLOCK)["timestamp"],
        tz=datetime.UTC,
    ).replace(tzinfo=None)
    read = reader.process_result(BUIDL_TEST_BLOCK, timestamp, call_results)

    assert read.share_price == BUIDL_ESTIMATED_NAV_PER_SHARE
    assert read.total_supply == BUIDL_EXPECTED_TOTAL_SUPPLY
    assert read.total_assets == BUIDL_EXPECTED_TOTAL_SUPPLY
    assert read.management_fee is None
    assert read.performance_fee is None
    assert read.errors is None


def test_unpriced_dstoken_historical_reader_returns_error(web3: Web3) -> None:
    """Do not abort a shared history scan for a DSToken without a NAV source."""

    vault = SecuritizeVault(
        web3,
        VaultSpec(chain_id=8453, vault_address=BUIDL_ETHEREUM_ADDRESS),
    )
    reader = vault.get_historical_reader(stateful=False)
    call_results = [call.call_as_result(web3, block_identifier=BUIDL_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(
        web3.eth.get_block(BUIDL_TEST_BLOCK)["timestamp"],
        tz=datetime.UTC,
    ).replace(tzinfo=None)

    read = reader.process_result(BUIDL_TEST_BLOCK, timestamp, call_results)

    assert not vault.is_buidl
    assert vault.product is None
    assert read.share_price is None
    assert read.total_assets is None
    assert read.total_supply == BUIDL_EXPECTED_TOTAL_SUPPLY
    assert read.errors == [f"No on-chain NAV source configured for Securitize DSToken {vault.address}"]
    assert read.deposits_open is False
    assert read.redemption_open is False

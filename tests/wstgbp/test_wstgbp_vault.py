"""Test wstGBP vault integration on an Ethereum Anvil fork."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.wstgbp.constants import WSTGBP
from eth_defi.wstgbp.historical import WSTGBPVaultHistoricalReader
from eth_defi.wstgbp.vault import WSTGBP_NAV_SOURCE, WSTGBP_NOTE, WSTGBPVault

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Fixed Ethereum mainnet block for deterministic wstGBP assertions.
WSTGBP_TEST_BLOCK = 25_532_314

WSTGBP_EXPECTED_GEM = "0x27f6c8289550fCE67f6B50BeD1F519966aFE5287"
WSTGBP_EXPECTED_DECIMALS = 18
WSTGBP_EXPECTED_RAW_SUPPLY = 20_125_083_675_286_769_012_566
WSTGBP_EXPECTED_TOTAL_SUPPLY = Decimal("20125.083675286769012566")
WSTGBP_EXPECTED_SHARE_PRICE = Decimal("1.005372972418361109")
WSTGBP_EXPECTED_TOTAL_ASSETS = Decimal("20233.21519479129423955175334")
WSTGBP_EXPECTED_WITHDRAW_FEE = 0.0025


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at a fixed wstGBP block."""

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=WSTGBP_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create an RPC connection to the fixed Anvil fork."""

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_wstgbp_hardcoded_detection() -> None:
    """Classify the known wstGBP contract through its hardcoded address."""

    assert HARDCODED_PROTOCOLS[WSTGBP.vault] == {ERC4626Feature.wstgbp_like}


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")
def test_wstgbp_metadata_and_fees(web3: Web3) -> None:
    """Read wstGBP metadata, market state and mint/redeem fees."""

    vault = create_vault_instance_autodetect(web3, WSTGBP.vault)

    assert isinstance(vault, WSTGBPVault)
    assert vault.features == {ERC4626Feature.wstgbp_like}
    assert vault.get_protocol_name() == "wstGBP"
    assert vault.get_risk() is VaultTechnicalRisk.low
    assert vault.name == "Wren Staked tGBP"
    assert vault.symbol == "wstGBP"
    assert vault.share_token.decimals == WSTGBP_EXPECTED_DECIMALS
    assert vault.fetch_denomination_token_address() == Web3.to_checksum_address(WSTGBP_EXPECTED_GEM)
    assert vault.denomination_token.symbol == "tGBP"
    assert vault.get_link() == "https://wstgbp.com"

    info = vault.fetch_info()
    assert info["denomination_token"] == Web3.to_checksum_address(WSTGBP_EXPECTED_GEM)
    assert info["nav_source"] == WSTGBP_NAV_SOURCE
    assert info["nav_estimated"] is False
    assert vault.get_fee_data().fee_mode == VaultFeeMode.externalised
    assert vault.get_fee_data().management is None
    assert vault.get_fee_data().performance is None
    assert vault.get_fee_data().deposit == 0
    assert vault.get_fee_data().withdraw == pytest.approx(WSTGBP_EXPECTED_WITHDRAW_FEE)
    assert vault.fetch_deposit_closed_reason() is None
    assert vault.fetch_redemption_closed_reason() is None
    assert vault.get_notes() == WSTGBP_NOTE

    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()

    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")
def test_wstgbp_historical_reader(web3: Web3) -> None:
    """Calculate historical wstGBP TVL from supply and the onchain NAV/share."""

    vault = create_vault_instance_autodetect(web3, WSTGBP.vault)
    reader = vault.get_historical_reader(stateful=False)

    assert isinstance(reader, WSTGBPVaultHistoricalReader)
    assert vault.fetch_total_supply(WSTGBP_TEST_BLOCK) == WSTGBP_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_share_price(WSTGBP_TEST_BLOCK) == WSTGBP_EXPECTED_SHARE_PRICE
    assert vault.fetch_total_assets(WSTGBP_TEST_BLOCK) == WSTGBP_EXPECTED_TOTAL_ASSETS
    assert vault.fetch_nav(WSTGBP_TEST_BLOCK) == WSTGBP_EXPECTED_TOTAL_ASSETS

    raw_supply = vault.share_token.contract.functions.totalSupply().call(block_identifier=WSTGBP_TEST_BLOCK)
    assert raw_supply == WSTGBP_EXPECTED_RAW_SUPPLY

    call_results = [call.call_as_result(web3, block_identifier=WSTGBP_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    assert {result.call.extra_data["function"] for result in call_results} == {
        "totalSupply",
        "navprice",
    }
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(WSTGBP_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(
        block_number=WSTGBP_TEST_BLOCK,
        timestamp=timestamp,
        call_results=call_results,
    )

    assert read.share_price == WSTGBP_EXPECTED_SHARE_PRICE
    assert read.total_supply == WSTGBP_EXPECTED_TOTAL_SUPPLY
    assert read.total_assets == WSTGBP_EXPECTED_TOTAL_ASSETS
    assert read.deposits_open is True
    assert read.redemption_open is True
    assert read.performance_fee is None
    assert read.management_fee is None
    assert read.errors is None


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")
@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")
def test_wstgbp_scan_record(web3: Web3) -> None:
    """Create a shared vault scan record for the hardcoded Wren Staked tGBP lead."""

    detection = ERC4262VaultDetection(
        chain=web3.eth.chain_id,
        address=WSTGBP.vault,
        first_seen_at_block=WSTGBP.first_seen_at_block,
        first_seen_at=WSTGBP.first_seen_at,
        features={ERC4626Feature.wstgbp_like},
        updated_at=WSTGBP.first_seen_at,
        deposit_count=0,
        redeem_count=0,
    )
    record = create_vault_scan_record(
        web3,
        detection,
        block_identifier=WSTGBP_TEST_BLOCK,
        token_cache={},
    )

    assert record["Protocol"] == "wstGBP"
    assert record["Symbol"] == "wstGBP"
    assert record["Denomination"] == "tGBP"
    assert record["Share token"] == "wstGBP"
    assert record["NAV"] == WSTGBP_EXPECTED_TOTAL_ASSETS
    assert record["Shares"] == WSTGBP_EXPECTED_TOTAL_SUPPLY
    assert record["Mgmt fee"] is None
    assert record["Perf fee"] is None
    assert record["Deposit fee"] == 0
    assert record["Withdraw fee"] == pytest.approx(WSTGBP_EXPECTED_WITHDRAW_FEE)
    assert record["Features"] == "wstgbp_like"
    assert record["_denomination_token"]["address"] == Web3.to_checksum_address(WSTGBP_EXPECTED_GEM)
    assert record["_denomination_token"]["symbol"] == "tGBP"
    assert "_wstgbp_mintable" not in record
    assert "_wstgbp_burnable" not in record
    assert "_wstgbp_cooldown" not in record

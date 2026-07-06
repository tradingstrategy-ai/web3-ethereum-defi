"""Test ODA-FACT vault tracking against the live JLTXX contract."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import (
    ODA_FACT_JLTXX_ADDRESS,
    ODA_FACT_JLTXX_FIRST_SEEN_AT,
    ODA_FACT_JLTXX_FIRST_SEEN_AT_BLOCK,
    create_vault_instance_autodetect,
)
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.oda_fact.historical import OdaFactVaultHistoricalReader
from eth_defi.oda_fact.vault import KINEXYS_WHITELISTED_FLOW_REASON, OdaFactVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JLTXX_EXPECTED_MANAGEMENT_FEE = 0.0016
JLTXX_EXPECTED_PERFORMANCE_FEE = 0
JLTXX_EXPECTED_GROSS_EXPENSE_RATIO = 0.0071
JLTXX_EXPECTED_PROSPECTUS_MANAGEMENT_FEE = 0.0008
JLTXX_EXPECTED_PROSPECTUS_SERVICE_FEE = 0.001

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

#: Fixed block used for deterministic JLTXX assertions.
#:
#: Blockscout token metadata reported raw total supply ``69522262199`` for
#: JLTXX when this integration was written.
JLTXX_TEST_BLOCK = 25_452_271

#: JLTXX has 2 decimals.
JLTXX_EXPECTED_DECIMALS = 2

#: Ethereum USDC has 6 decimals.
USDC_EXPECTED_DECIMALS = 6

#: Raw total supply for JLTXX at :py:data:`JLTXX_TEST_BLOCK`.
JLTXX_EXPECTED_RAW_TOTAL_SUPPLY = 69_522_262_199

#: Human-readable total supply at :py:data:`JLTXX_TEST_BLOCK`.
JLTXX_EXPECTED_TOTAL_SUPPLY = Decimal("695222621.99")


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum mainnet at a fixed block for JLTXX integration tests."""

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=JLTXX_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 connection to the fixed-block Anvil fork."""

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


@flaky.flaky
def test_oda_fact_autodetect_live_jltxx(web3: Web3) -> None:
    """Autodetect the live JLTXX ODA-FACT contract."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=ODA_FACT_JLTXX_ADDRESS,
    )

    assert isinstance(vault, OdaFactVault)
    assert vault.features == {ERC4626Feature.oda_fact_like}
    assert vault.get_protocol_name() == "Kinexys"
    assert vault.address == Web3.to_checksum_address(ODA_FACT_JLTXX_ADDRESS)
    assert vault.share_token.name == "JPMorgan OnChain Liquidity-Token Money Market Fund"
    assert vault.share_token.symbol == "JLTXX"
    assert vault.share_token.decimals == JLTXX_EXPECTED_DECIMALS
    assert vault.denomination_token.symbol == "USDC"
    assert vault.denomination_token.decimals == USDC_EXPECTED_DECIMALS


@flaky.flaky
def test_oda_fact_live_supply_nav_and_unsupported_actions(web3: Web3) -> None:
    """Read live JLTXX supply/NAV and ensure active flows stay unsupported."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=ODA_FACT_JLTXX_ADDRESS,
    )

    raw_supply = vault.share_token.contract.functions.totalSupply().call(block_identifier=JLTXX_TEST_BLOCK)
    assert raw_supply == JLTXX_EXPECTED_RAW_TOTAL_SUPPLY

    assert vault.fetch_share_price(JLTXX_TEST_BLOCK) == Decimal("1")
    assert vault.fetch_total_supply(JLTXX_TEST_BLOCK) == JLTXX_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_total_assets(JLTXX_TEST_BLOCK) == JLTXX_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_nav(JLTXX_TEST_BLOCK) == JLTXX_EXPECTED_TOTAL_SUPPLY
    assert vault.get_fee_data().management == JLTXX_EXPECTED_MANAGEMENT_FEE
    assert vault.get_fee_data().performance == JLTXX_EXPECTED_PERFORMANCE_FEE
    assert vault.get_fee_data().deposit == 0
    assert vault.get_fee_data().withdraw == 0
    assert vault.fetch_info()["nav_source"] == "estimated_jltxx_usd_1"
    assert vault.fetch_info()["nav_estimated"] is True
    assert "**Curator:** J.P. Morgan" in vault.get_notes()
    assert "JLTXX fact sheet" in vault.get_notes()

    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()

    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


@flaky.flaky
def test_oda_fact_historical_reader_live_jltxx(web3: Web3) -> None:
    """Read a historical JLTXX sample through the ODA-FACT historical reader."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=ODA_FACT_JLTXX_ADDRESS,
    )
    reader = vault.get_historical_reader(stateful=False)

    assert isinstance(reader, OdaFactVaultHistoricalReader)

    call_results = [
        call.call_as_result(
            web3,
            block_identifier=JLTXX_TEST_BLOCK,
            ignore_error=True,
        )
        for call in reader.construct_multicalls()
    ]
    timestamp = datetime.datetime.fromtimestamp(
        web3.eth.get_block(JLTXX_TEST_BLOCK)["timestamp"],
        tz=datetime.UTC,
    ).replace(tzinfo=None)
    read = reader.process_result(
        block_number=JLTXX_TEST_BLOCK,
        timestamp=timestamp,
        call_results=call_results,
    )

    assert read.block_number == JLTXX_TEST_BLOCK
    assert read.share_price == Decimal("1")
    assert read.total_supply == JLTXX_EXPECTED_TOTAL_SUPPLY
    assert read.total_assets == JLTXX_EXPECTED_TOTAL_SUPPLY
    assert read.performance_fee == JLTXX_EXPECTED_PERFORMANCE_FEE
    assert read.management_fee == JLTXX_EXPECTED_MANAGEMENT_FEE
    assert read.errors is None


@flaky.flaky
def test_oda_fact_scan_record_live_jltxx(web3: Web3) -> None:
    """Create the shared vault scan record for JLTXX."""

    detection = ERC4262VaultDetection(
        chain=web3.eth.chain_id,
        address=ODA_FACT_JLTXX_ADDRESS,
        first_seen_at_block=ODA_FACT_JLTXX_FIRST_SEEN_AT_BLOCK,
        first_seen_at=ODA_FACT_JLTXX_FIRST_SEEN_AT,
        features={ERC4626Feature.oda_fact_like},
        updated_at=ODA_FACT_JLTXX_FIRST_SEEN_AT,
        deposit_count=0,
        redeem_count=0,
    )

    record = create_vault_scan_record(
        web3,
        detection,
        block_identifier=JLTXX_TEST_BLOCK,
        token_cache={},
    )

    assert record["Symbol"] == "JLTXX"
    assert record["Name"] == "JPMorgan OnChain Liquidity-Token Money Market Fund"
    assert record["Protocol"] == "Kinexys"
    assert record["Denomination"] == "USDC"
    assert record["Share token"] == "JLTXX"
    assert record["NAV"] == JLTXX_EXPECTED_TOTAL_SUPPLY
    assert record["Mgmt fee"] == JLTXX_EXPECTED_MANAGEMENT_FEE
    assert record["Perf fee"] == JLTXX_EXPECTED_PERFORMANCE_FEE
    assert record["Deposit fee"] == 0
    assert record["Withdraw fee"] == 0
    assert record["Shares"] == JLTXX_EXPECTED_TOTAL_SUPPLY
    assert record["Features"] == "oda_fact_like"
    assert record["_detection_data"] == detection
    assert record["_denomination_token"]["symbol"] == "USDC"
    assert record["_share_token"]["symbol"] == "JLTXX"
    assert record["_manager_name"] == "J.P. Morgan Kinexys"
    assert record["_deposit_closed_reason"] == KINEXYS_WHITELISTED_FLOW_REASON
    assert record["_redemption_closed_reason"] == KINEXYS_WHITELISTED_FLOW_REASON
    assert "**Curator:** J.P. Morgan" in record["_notes"]
    assert "JLTXX fact sheet" in record["_notes"]
    assert record["_nav_source"] == "estimated_jltxx_usd_1"
    assert record["_nav_estimated"] is True
    assert record["_synthetic_usd_denomination"] is True
    assert record["_gross_expense_ratio"] == JLTXX_EXPECTED_GROSS_EXPENSE_RATIO
    assert record["_net_expense_ratio"] == JLTXX_EXPECTED_MANAGEMENT_FEE
    assert record["_prospectus_management_fee"] == JLTXX_EXPECTED_PROSPECTUS_MANAGEMENT_FEE
    assert record["_prospectus_service_fee"] == JLTXX_EXPECTED_PROSPECTUS_SERVICE_FEE
    assert record["_fee_waiver_until"] == "2028-06-30"
    assert record["_fee_source"].startswith("https://www.sec.gov/")
    assert record["_fee_fact_sheet"].endswith("fs-ocltmm-t.pdf")

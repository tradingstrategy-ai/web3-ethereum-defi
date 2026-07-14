"""Test Maseer One wstGBP vault integration on an Ethereum Anvil fork."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.maseer_one.constants import MASEER_ONE_WSTGBP
from eth_defi.maseer_one.historical import MaseerOneVaultHistoricalReader
from eth_defi.maseer_one.vault import MASEER_ONE_BESPOKE_FLOW_REASON, MASEER_ONE_NAV_SOURCE, MaseerOneVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.fee import VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Fixed Ethereum mainnet block for deterministic Maseer One assertions.
MASEER_ONE_TEST_BLOCK = 25_532_314

WSTGBP_EXPECTED_GEM = "0x27f6c8289550fCE67f6B50BeD1F519966aFE5287"
WSTGBP_EXPECTED_DECIMALS = 18
WSTGBP_EXPECTED_RAW_SUPPLY = 20_125_083_675_286_769_012_566
WSTGBP_EXPECTED_TOTAL_SUPPLY = Decimal("20125.083675286769012566")
WSTGBP_EXPECTED_SHARE_PRICE = Decimal("1.005372972418361109")
WSTGBP_EXPECTED_TOTAL_ASSETS = Decimal("20233.21519479129423955175334")
WSTGBP_EXPECTED_WITHDRAW_FEE = Decimal("0.002499999999999999231628439203")


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at a fixed Maseer One block."""

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=MASEER_ONE_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create an RPC connection to the fixed Anvil fork."""

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_maseer_one_hardcoded_detection() -> None:
    """Classify the known wstGBP contract through its hardcoded address."""

    assert HARDCODED_PROTOCOLS[MASEER_ONE_WSTGBP.vault] == {ERC4626Feature.maseer_one_like}


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")
def test_maseer_one_closed_reasons_use_default_block_identifier(
    web3: Web3,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the pinned vault block when evaluating mint and burn availability.

    The historical reader needs availability flags from the same block as its
    TVL and share-price reads. This confirms the user-facing reason methods
    do not silently read the latest state.

    :param web3:
        Anvil Ethereum mainnet fork connection.
    :param monkeypatch:
        Pytest fixture for replacing RPC-backed availability reads.
    """

    vault = create_vault_instance(
        web3,
        MASEER_ONE_WSTGBP.vault,
        features={ERC4626Feature.maseer_one_like},
        default_block_identifier=MASEER_ONE_TEST_BLOCK,
    )
    assert isinstance(vault, MaseerOneVault)

    mint_blocks: list[BlockIdentifier] = []
    burn_blocks: list[BlockIdentifier] = []

    def fetch_mintable(block_identifier: BlockIdentifier) -> bool:
        mint_blocks.append(block_identifier)
        return False

    def fetch_burnable(block_identifier: BlockIdentifier) -> bool:
        burn_blocks.append(block_identifier)
        return False

    monkeypatch.setattr(vault, "fetch_mintable", fetch_mintable)
    monkeypatch.setattr(vault, "fetch_burnable", fetch_burnable)

    assert vault.fetch_deposit_closed_reason() == "Maseer One minting is currently disabled"
    assert vault.fetch_redemption_closed_reason() == "Maseer One redemption is currently disabled"
    assert mint_blocks == [MASEER_ONE_TEST_BLOCK]
    assert burn_blocks == [MASEER_ONE_TEST_BLOCK]


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")
def test_maseer_one_wstgbp_metadata_and_fees(web3: Web3) -> None:
    """Read wstGBP metadata, market state and the Maseer One mint/redeem spread."""

    vault = create_vault_instance_autodetect(web3, MASEER_ONE_WSTGBP.vault)

    assert isinstance(vault, MaseerOneVault)
    assert vault.features == {ERC4626Feature.maseer_one_like}
    assert vault.get_protocol_name() == "Maseer One"
    assert vault.name == "Wren Staked tGBP"
    assert vault.symbol == "wstGBP"
    assert vault.share_token.decimals == WSTGBP_EXPECTED_DECIMALS
    assert vault.fetch_denomination_token_address() == Web3.to_checksum_address(WSTGBP_EXPECTED_GEM)
    assert vault.denomination_token.symbol == "tGBP"
    assert vault.get_link() == "https://docs.wstgbp.com/"

    info = vault.fetch_info()
    assert info["denomination_token"] == Web3.to_checksum_address(WSTGBP_EXPECTED_GEM)
    assert info["nav_source"] == MASEER_ONE_NAV_SOURCE
    assert info["nav_estimated"] is False
    assert info["mintable"] is True
    assert info["burnable"] is True
    assert info["cooldown"] == 0

    assert vault.get_fee_data().fee_mode == VaultFeeMode.externalised
    assert vault.get_fee_data().management is None
    assert vault.get_fee_data().performance is None
    assert vault.get_fee_data().deposit == 0
    assert vault.get_fee_data().withdraw == WSTGBP_EXPECTED_WITHDRAW_FEE
    assert vault.fetch_deposit_closed_reason() == MASEER_ONE_BESPOKE_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == MASEER_ONE_BESPOKE_FLOW_REASON

    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()

    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")
def test_maseer_one_wstgbp_historical_reader(web3: Web3) -> None:
    """Calculate historical wstGBP TVL from supply and the on-chain NAV/share."""

    vault = create_vault_instance_autodetect(web3, MASEER_ONE_WSTGBP.vault)
    reader = vault.get_historical_reader(stateful=False)

    assert isinstance(reader, MaseerOneVaultHistoricalReader)
    assert vault.fetch_total_supply(MASEER_ONE_TEST_BLOCK) == WSTGBP_EXPECTED_TOTAL_SUPPLY
    assert vault.fetch_share_price(MASEER_ONE_TEST_BLOCK) == WSTGBP_EXPECTED_SHARE_PRICE
    assert vault.fetch_total_assets(MASEER_ONE_TEST_BLOCK) == WSTGBP_EXPECTED_TOTAL_ASSETS
    assert vault.fetch_nav(MASEER_ONE_TEST_BLOCK) == WSTGBP_EXPECTED_TOTAL_ASSETS

    raw_supply = vault.share_token.contract.functions.totalSupply().call(block_identifier=MASEER_ONE_TEST_BLOCK)
    assert raw_supply == WSTGBP_EXPECTED_RAW_SUPPLY

    call_results = [call.call_as_result(web3, block_identifier=MASEER_ONE_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    assert {result.call.extra_data["function"] for result in call_results} == {
        "totalSupply",
        "navprice",
        "mintable",
        "burnable",
    }
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(MASEER_ONE_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(
        block_number=MASEER_ONE_TEST_BLOCK,
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
def test_maseer_one_historical_reader_records_market_gate_failures(web3: Web3) -> None:
    """Keep historical TVL rows when the market-gate calls revert.

    Historical reads use an error-tolerant multicall batch. Market-gate
    failures must be represented as unavailable status and error text rather
    than making the whole scanner cycle fail.

    :param web3:
        Anvil Ethereum mainnet fork connection.
    """

    vault = create_vault_instance_autodetect(web3, MASEER_ONE_WSTGBP.vault)
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, MaseerOneVaultHistoricalReader)

    call_results = [call.call_as_result(web3, block_identifier=MASEER_ONE_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    for result in call_results:
        if result.call.extra_data["function"] in {"mintable", "burnable"}:
            result.success = False
            result.result = b""

    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(MASEER_ONE_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(
        block_number=MASEER_ONE_TEST_BLOCK,
        timestamp=timestamp,
        call_results=call_results,
    )

    assert read.total_assets == WSTGBP_EXPECTED_TOTAL_ASSETS
    assert read.deposits_open is None
    assert read.redemption_open is None
    assert read.errors == ["Maseer One mintable call failed", "Maseer One burnable call failed"]


@flaky.flaky
@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")
def test_maseer_one_wstgbp_scan_record(web3: Web3) -> None:
    """Create a shared vault scan record for the hardcoded Maseer One lead."""

    detection = ERC4262VaultDetection(
        chain=web3.eth.chain_id,
        address=MASEER_ONE_WSTGBP.vault,
        first_seen_at_block=MASEER_ONE_WSTGBP.first_seen_at_block,
        first_seen_at=MASEER_ONE_WSTGBP.first_seen_at,
        features={ERC4626Feature.maseer_one_like},
        updated_at=MASEER_ONE_WSTGBP.first_seen_at,
        deposit_count=0,
        redeem_count=0,
    )
    record = create_vault_scan_record(
        web3,
        detection,
        block_identifier=MASEER_ONE_TEST_BLOCK,
        token_cache={},
    )

    assert record["Protocol"] == "Maseer One"
    assert record["Symbol"] == "wstGBP"
    assert record["Denomination"] == "tGBP"
    assert record["Share token"] == "wstGBP"
    assert record["NAV"] == WSTGBP_EXPECTED_TOTAL_ASSETS
    assert record["Shares"] == WSTGBP_EXPECTED_TOTAL_SUPPLY
    assert record["Mgmt fee"] is None
    assert record["Perf fee"] is None
    assert record["Deposit fee"] == 0
    assert record["Withdraw fee"] == WSTGBP_EXPECTED_WITHDRAW_FEE
    assert record["Features"] == "maseer_one_like"
    assert record["_denomination_token"]["address"] == Web3.to_checksum_address(WSTGBP_EXPECTED_GEM)
    assert record["_denomination_token"]["symbol"] == "tGBP"
    assert record["_maseer_one_mintable"] is True
    assert record["_maseer_one_burnable"] is True
    assert record["_maseer_one_cooldown"] == 0

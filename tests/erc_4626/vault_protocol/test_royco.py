"""Test Royco Protocol and ZeroLend vault metadata."""

import datetime
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR, get_topic_signature_from_event
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import VaultEventKind, get_royco_tranche_discovery_events, get_vault_event_topic_map
from eth_defi.erc_4626.vault_protocol.royco.offchain_metadata import fetch_royco_vaults
from eth_defi.erc_4626.vault_protocol.royco.vault import RoycoTrancheHistoricalReader, RoycoTrancheVault, RoycoVault
from eth_defi.erc_4626.vault_protocol.zerolend.vault import ZeroLendVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
ROYCO_TRANCHE_BLOCK = 25_251_545
ROYCO_JUNIOR_TRANCHE = "0x059bc7aa5000a26aae2601cfbf060653adf8fd91"
ROYCO_SENIOR_TRANCHE = "0x1ba515a409dd702105415cdaae439059aa0b402a"

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24167930)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@pytest.fixture(scope="module")
def anvil_ethereum_royco_tranche_fork() -> AnvilLaunch:
    """Fork after Royco tranche vault deployment."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=ROYCO_TRANCHE_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def royco_tranche_web3(anvil_ethereum_royco_tranche_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_royco_tranche_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_zerolend_royco_vault(
    web3: Web3,
):
    """Read ZeroLend vault metadata.

    ZeroLend RWA USDC vault wrapped by Royco.
    This vault has both zerolend_like and royco_like features.
    https://etherscan.io/address/0x887d57a509070a0843c6418eb5cffc090dcbbe95

    1. Create a vault instance using ERC-4626 autodetection.
    2. Verify the more specific ZeroLend adapter is selected.
    3. Verify Royco and ZeroLend feature flags are both present.
    4. Check basic fee, link, deposit, redeem and capability metadata.
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x887d57a509070a0843c6418eb5cffc090dcbbe95",
    )

    # ZeroLendVault is a subclass of RoycoVault
    assert isinstance(vault, ZeroLendVault)
    assert isinstance(vault, RoycoVault)

    # Protocol name should be ZeroLend (more specific)
    assert vault.get_protocol_name() == "ZeroLend"

    # Both features should be present
    assert ERC4626Feature.zerolend_like in vault.features
    assert ERC4626Feature.royco_like in vault.features

    # Fees are handled by the underlying vault (inherited from RoycoVault)
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Link should point to ZeroLend
    assert vault.get_link() == "https://zerolend.xyz/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # ZeroLend/Royco doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False


def test_royco_tranche_redeem_topic_is_withdraw():
    """Royco tranche custom Redeem event is treated as a withdrawal lead.

    1. Build Royco tranche discovery events.
    2. Derive the custom ``Redeem`` event topic.
    3. Verify the topic is mapped to a withdrawal event kind.
    """
    web3 = Web3()
    royco_events = get_royco_tranche_discovery_events(web3)
    redeem_topic = get_topic_signature_from_event(royco_events[0])
    topic_map = get_vault_event_topic_map(web3)

    assert redeem_topic == "0xf4cb7c6504cf537b604bc6d8493f84b29dd478c0726340f5802828f61b324747"
    assert topic_map[redeem_topic] == VaultEventKind.withdraw


@flaky.flaky
@pytest.mark.parametrize(
    ("vault_address", "tranche_type", "expected_nav", "expected_share_price", "expected_total_supply", "expected_max_deposit"),
    [
        (
            ROYCO_JUNIOR_TRANCHE,
            1,
            Decimal("112977.546343749885828324"),
            Decimal("1.011041852920180597"),
            Decimal("111743.688965435143396373"),
            Decimal("1.157920892373161954235709850E+71"),
        ),
        (
            ROYCO_SENIOR_TRANCHE,
            0,
            Decimal("904838.104129907413558521"),
            Decimal("1.004272285312968733"),
            Decimal("900988.822815045705371738"),
            Decimal("110030.837687"),
        ),
    ],
)
def test_royco_tranche_vault(
    royco_tranche_web3: Web3,
    vault_address: str,
    tranche_type: int,
    expected_nav: Decimal,
    expected_share_price: Decimal,
    expected_total_supply: Decimal,
    expected_max_deposit: Decimal,
):
    """Read Royco senior/junior tranche values using tuple-aware adapters.

    1. Create a vault instance via ERC-4626 autodetection.
    2. Verify protocol name, feature flags and tranche type.
    3. Fetch and assert current NAV and share price.
    4. Exercise historical reader multicalls at the pinned fork block.
    5. Assert historical values exactly for the fixed Anvil fork.
    """
    vault = create_vault_instance_autodetect(
        royco_tranche_web3,
        vault_address=vault_address,
    )

    assert isinstance(vault, RoycoTrancheVault)
    assert vault.get_protocol_name() == "Royco"
    assert ERC4626Feature.royco_tranche_like in vault.features
    assert vault.fetch_tranche_type() == tranche_type

    assert vault.fetch_total_assets("latest") == expected_nav
    assert vault.fetch_nav("latest") == expected_nav
    assert vault.fetch_share_price("latest") == expected_share_price

    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, RoycoTrancheHistoricalReader)

    block_number = royco_tranche_web3.eth.block_number
    block = royco_tranche_web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.timezone.utc).replace(tzinfo=None)

    calls = list(reader.construct_multicalls())
    call_results = [c.call_as_result(web3=royco_tranche_web3, block_identifier=block_number) for c in calls]
    vault_read = reader.process_result(block_number, timestamp, call_results)

    assert vault_read.block_number == ROYCO_TRANCHE_BLOCK
    assert vault_read.share_price == expected_share_price
    assert vault_read.total_assets == expected_nav
    assert vault_read.total_supply == expected_total_supply
    assert vault_read.max_deposit == expected_max_deposit
    assert vault_read.errors is None


def test_royco_offchain_metadata_cache(tmp_path: Path):
    """Fetch Royco metadata from both first-party vault API surfaces.

    1. Mock Royco ``vault/explore`` and ``market/explore`` API responses.
    2. Fetch and merge Royco metadata through the cache helper.
    3. Verify both API surfaces are normalised by checksum vault address.
    """
    vault_response = Mock()
    vault_response.raise_for_status.return_value = None
    vault_response.json.return_value = {
        "page": {"index": 1, "size": 500, "total": 1},
        "count": 1,
        "data": [
            {
                "id": "1_0x74d1fafa4e0163b2f1035f1b052137f3f9bad5cc",
                "chainId": 1,
                "vaultAddress": "0x74d1fafa4e0163b2f1035f1b052137f3f9bad5cc",
                "name": "Roy USDC Mainnet",
                "description": "Deposit assets to earn highest yields.",
                "isVerified": True,
                "tvlUsd": 46.99952027437871,
                "sharePrice": "1.000000",
                "lastUpdated": "2025-04-03 05:30:01",
            }
        ],
    }

    market_response = Mock()
    market_response.raise_for_status.return_value = None
    market_response.json.return_value = {
        "page": {"index": 1, "size": 500, "total": 1},
        "count": 1,
        "data": [
            {
                "id": "1_1_0x887d57a509070a0843c6418eb5cffc090dcbbe95",
                "chainId": 1,
                "marketType": 1,
                "marketId": "0x887d57a509070a0843c6418eb5cffc090dcbbe95",
                "underlyingVaultAddress": "0x0000000000000000000000000000000000000001",
                "name": "ZeroLend RWA USDC",
                "description": "Royco wrapped vault market",
                "isActive": True,
                "isVerified": True,
                "tvlUsd": 123.45,
                "lastUpdated": "2026-06-05 12:00:00",
            }
        ],
    }

    with patch("eth_defi.erc_4626.vault_protocol.royco.offchain_metadata.requests.post", side_effect=[vault_response, market_response]):
        metadata = fetch_royco_vaults(cache_path=tmp_path, api_key="ROYCO_DEMO")

    expected_vault_count = 2
    assert len(metadata) == expected_vault_count
    roy_usdc = metadata[Web3.to_checksum_address("0x74d1fafa4e0163b2f1035f1b052137f3f9bad5cc")]
    assert roy_usdc["source"] == "vault_explore"
    assert roy_usdc["name"] == "Roy USDC Mainnet"
    assert roy_usdc["share_price"] == "1.000000"

    zerolend = metadata[Web3.to_checksum_address("0x887d57a509070a0843c6418eb5cffc090dcbbe95")]
    assert zerolend["source"] == "market_explore"
    assert zerolend["is_active"] is True
    assert zerolend["underlying_vault_address"] == Web3.to_checksum_address("0x0000000000000000000000000000000000000001")

"""Test the reviewed Libeara CMTAT tokenised-fund integration."""

import datetime
import os
from decimal import Decimal
from types import SimpleNamespace

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance_autodetect, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.libeara.constants import BELIF_ETHEREUM, CUMIU_ETHEREUM, ETHEREUM_CHAIN_ID
from eth_defi.tokenised_fund.libeara.historical import LibearaVaultHistoricalReader, LibearaVaultReaderState
from eth_defi.tokenised_fund.libeara.vault import LIBEARA_RESTRICTED_FLOW_REASON, LibearaVault
from eth_defi.vault.curator import identify_curator

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

TEST_BLOCK = 25_553_275
EXPECTED = {
    CUMIU_ETHEREUM.token: (Decimal("5301526.7589"), Decimal("103.7369")),
    BELIF_ETHEREUM.token: (Decimal("102796944.2"), Decimal(1)),
}


@pytest.fixture(scope="module")
def web3() -> Web3:
    """Create an Ethereum archive-node client.

    :return: Archive-connected Web3 instance.
    """
    return create_multi_provider_web3(JSON_RPC_ETHEREUM)


def test_libeara_hardcoded_products_are_chain_aware() -> None:
    """Classify only the reviewed Ethereum CMTAT product proxies."""
    broken_probe = SimpleNamespace(success=True, result=b"")
    for product in (CUMIU_ETHEREUM, BELIF_ETHEREUM):
        assert HARDCODED_PROTOCOLS[product.token] == {ERC4626Feature.libeara_like}
        assert get_vault_protocol_name(HARDCODED_PROTOCOLS[product.token]) == "Libeara"
        assert identify_vault_features(product.token, calls={"EVM IS BROKEN SHIT": broken_probe}, debug_text="libeara", chain_id=ETHEREUM_CHAIN_ID) == {ERC4626Feature.libeara_like}
        assert identify_vault_features(product.token, calls={"EVM IS BROKEN SHIT": broken_probe}, debug_text="libeara", chain_id=31337) != {ERC4626Feature.libeara_like}
        assert identify_curator(ETHEREUM_CHAIN_ID, product.symbol, product.product_name, product.token, "libeara") == product.curator_slug


@pytest.mark.parametrize("product", [CUMIU_ETHEREUM, BELIF_ETHEREUM])
def test_libeara_adapter_reads_cmtat_nav(web3: Web3, product) -> None:
    """Read issuer NAV and supply at a fixed Ethereum archive block.

    :param web3: Archive-connected Ethereum client.
    :param product: Reviewed CMTAT product.
    """
    expected_supply, expected_price = EXPECTED[product.token]
    vault = create_vault_instance_autodetect(web3, product.token)
    assert isinstance(vault, LibearaVault)
    assert vault.manager_name == product.manager_name
    assert vault.curator_slug == product.curator_slug
    assert vault.fetch_total_supply(TEST_BLOCK) == expected_supply
    assert vault.fetch_share_price(TEST_BLOCK) == expected_price
    assert vault.fetch_total_assets(TEST_BLOCK) == expected_supply * expected_price
    assert vault.get_deposit_manager_capability() is None
    assert vault.fetch_deposit_closed_reason() == LIBEARA_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == LIBEARA_RESTRICTED_FLOW_REASON
    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()
    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


def test_libeara_historical_reader_uses_nav_scale(web3: Web3) -> None:
    """Apply the reported CMTAT NAV scaling factor in historical reads.

    :param web3: Archive-connected Ethereum client.
    """
    vault = create_vault_instance_autodetect(web3, CUMIU_ETHEREUM.token)
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, LibearaVaultHistoricalReader)
    results = [call.call_as_result(web3, block_identifier=TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(TEST_BLOCK, timestamp, results)
    assert read.total_supply == EXPECTED[CUMIU_ETHEREUM.token][0]
    assert read.share_price == EXPECTED[CUMIU_ETHEREUM.token][1]
    assert read.total_assets == EXPECTED[CUMIU_ETHEREUM.token][0] * EXPECTED[CUMIU_ETHEREUM.token][1]


def test_libeara_stateful_historical_reader_updates_scanner_state(web3: Web3) -> None:
    """Construct and update the state required by the production multicaller.

    :param web3:
        Archive-connected Ethereum client.
    """

    vault = create_vault_instance_autodetect(web3, CUMIU_ETHEREUM.token)
    reader = vault.get_historical_reader(stateful=True)
    assert isinstance(reader.reader_state, LibearaVaultReaderState)
    calls = list(reader.construct_multicalls())
    assert all(call.extra_data["vault"] == vault.address for call in calls)
    results = [call.call_as_result(web3, block_identifier=TEST_BLOCK, ignore_error=True) for call in calls]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    for result in results:
        result.timestamp = timestamp
    read = reader.process_result(TEST_BLOCK, timestamp, results)
    assert reader.reader_state.last_block == TEST_BLOCK
    assert reader.reader_state.last_share_price == read.share_price
    assert reader.reader_state.last_tvl == read.total_assets

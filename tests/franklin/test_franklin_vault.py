"""Test Franklin Templeton Benji Ethereum tokenised-fund support."""

import datetime
import os
from decimal import Decimal
from types import SimpleNamespace

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance_autodetect, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.tokenised_fund.franklin.constants import BENJI_ETHEREUM, ETHEREUM_CHAIN_ID, FRANKLIN_HARDCODED_LEADS, IBENJI_ETHEREUM
from eth_defi.tokenised_fund.franklin.historical import FranklinVaultHistoricalReader
from eth_defi.tokenised_fund.franklin.vault import FRANKLIN_RESTRICTED_FLOW_REASON, FranklinVault
from eth_defi.vault.curator import identify_curator

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")

#: Latest Ethereum block obtained through the repository get-block-number skill
#: on 2026-07-17. Both tokens reported a base-18 ``lastKnownPrice`` of one USD.
FRANKLIN_TEST_BLOCK = 25_553_079

EXPECTED_PRODUCTS = {
    IBENJI_ETHEREUM.token: {
        "name": "Franklin OnChain Institutional Liquidity Fund Ltd.",
        "symbol": "iBENJI",
        "supply": Decimal("121259870.905245330937566509"),
    },
    BENJI_ETHEREUM.token: {
        "name": "Franklin OnChain U.S. Government Money Fund",
        "symbol": "BENJI",
        "supply": Decimal("47880180.750413470304337021"),
    },
}


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at the reviewed Benji reference-price block.

    :return:
        Running Anvil fork.
    """

    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=FRANKLIN_TEST_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create Web3 for the fixed Anvil fork.

    :param anvil_ethereum_fork:
        Running Anvil fork.
    :return:
        Fork-connected Web3 instance.
    """

    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


def test_franklin_hardcoded_products_are_ethereum_only() -> None:
    """Register both official Ethereum tokens without inferring other chains."""

    assert FRANKLIN_HARDCODED_LEADS == (
        (ETHEREUM_CHAIN_ID, IBENJI_ETHEREUM.token, 22_118_491, IBENJI_ETHEREUM.first_seen_at),
        (ETHEREUM_CHAIN_ID, BENJI_ETHEREUM.token, 20_587_120, BENJI_ETHEREUM.first_seen_at),
    )
    for product in (IBENJI_ETHEREUM, BENJI_ETHEREUM):
        assert HARDCODED_PROTOCOLS[product.token] == {ERC4626Feature.franklin_like}
        assert get_vault_protocol_name(HARDCODED_PROTOCOLS[product.token]) == "Franklin Templeton"
        assert identify_curator(ETHEREUM_CHAIN_ID, product.symbol, product.product_name, product.token, "franklin") == "franklin-templeton"
        broken_probe = SimpleNamespace(success=True, result=b"")
        calls = {"EVM IS BROKEN SHIT": broken_probe}
        assert identify_vault_features(product.token, calls=calls, debug_text="franklin", chain_id=ETHEREUM_CHAIN_ID) == {ERC4626Feature.franklin_like}
        assert ERC4626Feature.franklin_like not in identify_vault_features(product.token, calls=calls, debug_text="non-ethereum", chain_id=31337)


@flaky.flaky
@pytest.mark.parametrize("token", [IBENJI_ETHEREUM.token, BENJI_ETHEREUM.token])
def test_franklin_benji_adapter_reads_issuer_price(web3: Web3, token: str) -> None:
    """Read both reviewed share tokens at a fixed fork block.

    :param web3:
        Ethereum Anvil fork connection.
    :param token:
        Official Ethereum Benji fund-token address.
    """

    expected = EXPECTED_PRODUCTS[token]
    vault = create_vault_instance_autodetect(web3, vault_address=token)

    assert isinstance(vault, FranklinVault)
    assert vault.features == {ERC4626Feature.franklin_like}
    assert vault.get_protocol_name() == "Franklin Templeton"
    assert vault.name == expected["name"]
    assert vault.symbol == expected["symbol"]
    assert vault.short_description == vault.product.short_description
    assert vault.description == vault.product.description
    assert vault.short_description != vault.description
    assert vault.fetch_total_supply(FRANKLIN_TEST_BLOCK) == expected["supply"]
    assert vault.fetch_share_price(FRANKLIN_TEST_BLOCK) == Decimal(1)
    assert vault.fetch_total_assets(FRANKLIN_TEST_BLOCK) == expected["supply"]
    assert vault.fetch_nav(FRANKLIN_TEST_BLOCK) == expected["supply"]
    assert vault.fetch_info()["nav_source"] == "last_known_price_usd_1e18"
    assert vault.fetch_deposit_closed_reason() == FRANKLIN_RESTRICTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == FRANKLIN_RESTRICTED_FLOW_REASON
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_fee_mode() is None
    assert vault.get_fee_data().fee_mode is None

    with pytest.raises(NotImplementedError):
        vault.get_deposit_manager()
    with pytest.raises(NotImplementedError):
        vault.get_flow_manager()


@flaky.flaky
def test_franklin_historical_reader_uses_same_block_price(web3: Web3) -> None:
    """Calculate BENJI historical TVL from supply and reference price.

    :param web3:
        Ethereum Anvil fork connection.
    """

    vault = create_vault_instance_autodetect(web3, vault_address=BENJI_ETHEREUM.token)
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, FranklinVaultHistoricalReader)
    call_results = [call.call_as_result(web3, block_identifier=FRANKLIN_TEST_BLOCK, ignore_error=True) for call in reader.construct_multicalls()]
    timestamp = datetime.datetime.fromtimestamp(web3.eth.get_block(FRANKLIN_TEST_BLOCK)["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    read = reader.process_result(FRANKLIN_TEST_BLOCK, timestamp, call_results)

    assert read.share_price == Decimal(1)
    assert read.total_supply == EXPECTED_PRODUCTS[BENJI_ETHEREUM.token]["supply"]
    assert read.total_assets == EXPECTED_PRODUCTS[BENJI_ETHEREUM.token]["supply"]
    assert read.errors is None

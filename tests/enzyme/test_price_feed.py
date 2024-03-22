"""Fetch enzyme price feeds.

"""

from decimal import Decimal
from functools import partial
from typing import List, cast

import pytest
from eth.constants import ZERO_ADDRESS
from eth_typing import HexAddress
from web3 import HTTPProvider, Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.events import Deposit, Redemption, fetch_vault_balance_events
from eth_defi.enzyme.price_feed import (
    EnzymePriceFeed,
    fetch_price_feeds,
    fetch_updated_price_feed,
)
from eth_defi.enzyme.uniswap_v2 import prepare_swap
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.reader import Web3EventReader, extract_events
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import (
    TransactionAssertionError,
    assert_call_success_with_explanation,
    assert_transaction_success_with_explanation,
)
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment


@pytest.fixture
def deployment(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    weth: Contract,
    mln: Contract,
    usdc: Contract,
    weth_usd_mock_chainlink_aggregator: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
) -> EnzymeDeployment:
    """Create Enzyme deployment that supports WETH and USDC tokens"""

    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        mln,
        weth,
    )

    tx_hash = deployment.add_primitive(
        usdc,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = deployment.add_primitive(
        weth,
        weth_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Set ethUsdAggregator needed for Enzyme's internal functionality
    tx_hash = deployment.contracts.value_interpreter.functions.setEthUsdAggregator(weth_usd_mock_chainlink_aggregator.address).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return deployment


def test_fetch_price_feeds(
    web3: Web3,
    deployment: EnzymeDeployment,
):
    """Fetch all deployed Enzyme price feeds."""

    provider = cast(HTTPProvider, web3.provider)
    json_rpc_url = provider.endpoint_uri
    reader = MultithreadEventReader(json_rpc_url, max_threads=16)

    start_block = 1
    end_block = web3.eth.block_number

    feed_iter = fetch_price_feeds(
        deployment,
        start_block,
        end_block,
        reader,
    )
    feeds = list(feed_iter)
    reader.close()
    assert len(feeds) == 2
    assert feeds[0].primitive_token.symbol == "USDC"
    assert feeds[1].primitive_token.symbol == "WETH"


def test_unsupported_base_asset(web3: Web3, deployment: EnzymeDeployment, weth: Contract, usdc: Contract, weth_usd_mock_chainlink_aggregator: Contract):
    """See what ValueInterpreter replies if it does not know about the asset"""

    # Check the underlying price feed is correctly configured
    # and print a Solidity stack trace of errors if any
    value_interpreter = deployment.contracts.value_interpreter
    raw_amount = 10**18
    with pytest.raises((ContractLogicError, ValueError), match="__calcAssetValue: Unsupported _baseAsset") as e:
        result = value_interpreter.functions.calcCanonicalAssetValue(
            ZERO_ADDRESS,
            raw_amount,
            usdc.address,
        ).call()


def test_manipulate_price(
    web3: Web3,
    deployment: EnzymeDeployment,
    weth: Contract,
    usdc: Contract,
    weth_usd_mock_chainlink_aggregator: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
):
    """Set the underlying price for Enzyme price feed."""

    weth_token = fetch_erc20_details(web3, weth.address)
    usdc_token = fetch_erc20_details(web3, usdc.address)
    feed = EnzymePriceFeed.fetch_price_feed(deployment, weth_token)

    # Check that our mocker is good
    mock_data = weth_usd_mock_chainlink_aggregator.functions.latestRoundData().call()
    assert len(mock_data) == 5

    call = usdc_usd_mock_chainlink_aggregator.functions.latestRoundData()
    mock_data = assert_call_success_with_explanation(call)
    assert len(mock_data) == 5

    # Check the underlying price feed is correctly configured
    # and print a Solidity stack trace of errors if any
    value_interpreter = deployment.contracts.value_interpreter
    raw_amount = weth_token.convert_to_raw(Decimal(1))
    res = value_interpreter.functions.calcCanonicalAssetValue(
        weth_token.address,
        raw_amount,
        usdc.address,
    ).call()

    price = feed.calculate_current_onchain_price(usdc_token)
    assert price == 1600

    # Bump the price a bit
    weth_usd_mock_chainlink_aggregator.functions.setValue(1500 * 10**8).transact()
    price = feed.calculate_current_onchain_price(usdc_token)
    assert price == 1500


def test_vault_denomination_token_price(
    web3: Web3,
    deployment: EnzymeDeployment,
    user_1: HexAddress,
    usdc_usd_mock_chainlink_aggregator: Contract,
    usdc: Contract,
):
    """Fetch the exchange rate for the vault base token."""

    comptroller_contract, vault_contract = deployment.create_new_vault(user_1, usdc, fund_name="Cow says Moo", fund_symbol="MOO")
    vault = Vault(vault_contract, comptroller_contract, deployment)
    assert vault.fetch_denomination_token_usd_exchange_rate() == 1


def test_remove_price_feeds(
    web3: Web3,
    deployment: EnzymeDeployment,
    usdc: Contract,
    weth: Contract,
):
    """Price feeds can be also deleted."""

    provider = cast(HTTPProvider, web3.provider)
    json_rpc_url = provider.endpoint_uri
    reader = MultithreadEventReader(json_rpc_url, max_threads=16)

    tx_hash = deployment.remove_primitive(usdc)
    assert_transaction_success_with_explanation(web3, tx_hash)

    start_block = 1
    end_block = web3.eth.block_number

    feeds = fetch_updated_price_feed(
        deployment,
        start_block,
        end_block,
        reader,
    )
    reader.close()
    assert len(feeds) == 2
    assert feeds[usdc.address].primitive_token.symbol == "USDC"
    assert feeds[usdc.address].added_block_number > 1
    assert feeds[usdc.address].removed_block_number > 1
    assert feeds[weth.address].primitive_token.symbol == "WETH"
    assert feeds[weth.address].removed_block_number is None

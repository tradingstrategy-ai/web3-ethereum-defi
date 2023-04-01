"""Fetch enzyme price feeds.

"""
from functools import partial
from typing import cast
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3, HTTPProvider
from web3.contract import Contract

from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.events import fetch_vault_balance_events, Deposit, Redemption
from eth_defi.enzyme.price_feed import fetch_price_feeds
from eth_defi.enzyme.uniswap_v2 import prepare_swap
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.reader import extract_events, Web3EventReader
from eth_defi.trace import assert_transaction_success_with_explanation, TransactionAssertionError
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

    deployment.add_primitive(
        usdc,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    deployment.add_primitive(
        weth,
        weth_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )
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

    assert len(feeds) == 0
    usdc_feed = feeds[0]
    assert usdc_feed.primitive_token.symbol == "USDC"

    reader.close()

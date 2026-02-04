"""Test Sentiment vault metadata"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.sentiment.vault import SentimentVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(JSON_RPC_HYPERLIQUID is None, reason="JSON_RPC_HYPERLIQUID needed to run these tests")


@pytest.fixture(scope="module")
def anvil_hyperliquid_fork(request) -> AnvilLaunch:
    """Fork HyperEVM at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_HYPERLIQUID, fork_block_number=24_884_293)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_hyperliquid_fork):
    web3 = create_multi_provider_web3(anvil_hyperliquid_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_sentiment(
    web3: Web3,
    tmp_path: Path,
):
    """Read Sentiment SuperPool vault metadata"""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xe45e7272da7208c7a137505dfb9491e330bf1a4e",
    )

    assert isinstance(vault, SentimentVault)
    assert vault.get_protocol_name() == "Sentiment"
    assert vault.features == {ERC4626Feature.sentiment_like}

    # Sentiment has fees taken from interest earned
    assert vault.has_custom_fees() is True
    assert vault.get_management_fee("latest") == 0.0

    # Performance fee should be readable from the contract
    perf_fee = vault.get_performance_fee("latest")
    assert perf_fee is not None
    assert 0 <= perf_fee <= 1.0  # Fee should be between 0% and 100%

    # Risk level is low for Sentiment
    assert vault.get_risk() == VaultTechnicalRisk.low

    # Test the link
    assert vault.get_link() == "https://app.sentiment.xyz/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # Sentiment doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False

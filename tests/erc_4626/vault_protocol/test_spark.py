"""Test Spark vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.erc_4626.vault_protocol.spark.vault import SparkVault
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_140_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_spark(
    web3: Web3,
    tmp_path: Path,
):
    """Read Spark vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe",
    )

    assert isinstance(vault, SparkVault)
    assert vault.get_protocol_name() == "Spark"
    assert vault.features == {ERC4626Feature.spark_like}

    # Spark does not charge fees
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://app.spark.fi/savings/mainnet/spusdc"

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.negligible


@flaky.flaky
def test_spark_pyusd(
    web3: Web3,
    tmp_path: Path,
):
    """Read Spark spPYUSD vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x80128dbb9f07b93dde62a6daeadb69ed14a7d354",
    )

    assert isinstance(vault, SparkVault)
    assert vault.get_protocol_name() == "Spark"
    assert vault.features == {ERC4626Feature.spark_like}

    # Spark does not charge fees
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.negligible

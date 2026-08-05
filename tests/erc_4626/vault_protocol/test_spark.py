"""Test Spark vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.spark.vault import SparkVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.xdist_group("fork:ethereum:24140000")


def test_spark_spusdg_hardcoded_protocol() -> None:
    """Classify the Robinhood Chain Spark Savings USDG vault by address."""
    vault_address = "0xde770c84fe66e063336b31737cfe9790f18c4087"

    features = HARDCODED_PROTOCOLS[vault_address]

    assert features == {ERC4626Feature.spark_like}
    assert get_vault_protocol_name(features) == "Spark"


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Share the read-only Spark fork and its warmed RPC cache."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, 24_140_000)


@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
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

    # Check maxDeposit and maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0
    assert vault.can_check_redeem() is False


@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
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

    # Check maxDeposit and maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0
    assert vault.can_check_redeem() is False

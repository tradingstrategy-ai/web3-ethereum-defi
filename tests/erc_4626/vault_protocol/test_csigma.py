"""Test cSigma Finance vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.csigma.vault import CsigmaVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=21_900_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_csigma(
    web3: Web3,
    tmp_path: Path,
):
    """Read cSigma Finance vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xd5d097f278a735d0a3c609deee71234cac14b47e",
    )

    assert isinstance(vault, CsigmaVault)
    assert vault.get_protocol_name() == "cSigma Finance"
    assert vault.features == {ERC4626Feature.csigma_like}

    # Fees are not yet known for cSigma
    assert vault.get_management_fee("latest") is 0
    assert vault.get_performance_fee("latest") is 0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://edge.csigma.finance/"

    # cSigma doesn't implement standard maxDeposit/maxRedeem (returns empty data)
    # so we cannot use address(0) checks for this vault
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_csigma_v2_pool(
    web3: Web3,
    tmp_path: Path,
):
    """Read cSigma Finance CsigmaV2Pool vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x438982ea288763370946625fd76c2508ee1fb229",
    )

    assert isinstance(vault, CsigmaVault)
    assert vault.get_protocol_name() == "cSigma Finance"
    assert vault.features == {ERC4626Feature.csigma_like}

    # Fees are not yet known for cSigma
    assert vault.get_management_fee("latest") is 0
    assert vault.get_performance_fee("latest") is 0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://edge.csigma.finance/"

    # cSigma doesn't implement standard maxDeposit/maxRedeem (returns empty data)
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_csigma_supqpv(
    web3: Web3,
    tmp_path: Path,
):
    """Read cSigma Finance cSuperior Quality Private Credit vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x50d59b785df23728d9948804f8ca3543237a1495",
    )

    assert isinstance(vault, CsigmaVault)
    assert vault.get_protocol_name() == "cSigma Finance"
    assert vault.features == {ERC4626Feature.csigma_like}

    # Fees are not yet known for cSigma
    assert vault.get_management_fee("latest") is 0
    assert vault.get_performance_fee("latest") is 0
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://edge.csigma.finance/"

    # cSigma doesn't implement standard maxDeposit/maxRedeem (returns empty data)
    assert vault.can_check_redeem() is False

"""Test Term Finance vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.term_finance.vault import TermFinanceVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.risk import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_141_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_term_finance_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Term Finance vault metadata."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xa10c40f9e318b0ed67ecc3499d702d8db9437228",
    )

    assert isinstance(vault, TermFinanceVault)
    assert vault.get_protocol_name() == "Term Finance"
    assert ERC4626Feature.term_finance_like in vault.features

    # Term Finance has internalised fees
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.has_custom_fees() is False

    # Check vault link
    assert vault.get_link() == "https://app.term.finance/vaults/0xa10c40f9e318b0ed67ecc3499d702d8db9437228/1"

    # Risk level is None (to be assessed later)
    assert vault.get_risk() is VaultTechnicalRisk.low

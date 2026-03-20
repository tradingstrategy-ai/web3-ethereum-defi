"""Test Inverse Finance sDOLA vault metadata.

- sDOLA is a yield-bearing ERC-4626 vault on Ethereum
- Yield comes from FiRM lending revenues via DBR auction
"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.inverse_finance.vault import InverseFinanceVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="JSON_RPC_ETHEREUM needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork Ethereum at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_700_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_inverse_finance(
    web3: Web3,
    tmp_path: Path,
):
    """Read Inverse Finance sDOLA vault metadata.

    1. Create vault instance via autodetection
    2. Verify correct vault class instantiation
    3. Check protocol name and feature flags
    4. Verify fee data (no fees)
    5. Check risk classification
    """

    # 1. Create vault instance via autodetection
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xb45ad160634c528Cc3D2926d9807104FA3157305",
    )

    # 2. Verify correct vault class instantiation
    assert isinstance(vault, InverseFinanceVault)

    # 3. Check protocol name and feature flags
    assert vault.get_protocol_name() == "Inverse Finance"
    assert vault.features == {ERC4626Feature.inverse_finance_like}

    # 4. Verify fee data (no fees)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.has_custom_fees() is False

    # 5. Check risk classification
    assert vault.get_risk() == VaultTechnicalRisk.severe

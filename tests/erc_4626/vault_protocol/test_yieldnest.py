"""Test YieldNest vault metadata"""

import datetime
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.yieldnest.vault import (
    YNRWAX_VAULT_ADDRESS, YieldNestVault)
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(not JSON_RPC_ETHEREUM, reason="JSON_RPC_ETHEREUM not set")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility

    Contract created at block 22,674,309 in June 2024
    Latest block as of 2026-01-15: 24,239,327
    """
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_239_327)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_yieldnest_ynrwax(
    web3: Web3,
    tmp_path: Path,
):
    """Read YieldNest ynRWAx vault metadata.

    This tests the hardcoded ynRWAx vault which is detected by address.
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=YNRWAX_VAULT_ADDRESS,
    )

    assert isinstance(vault, YieldNestVault)
    assert vault.get_protocol_name() == "YieldNest"
    assert vault.features == {ERC4626Feature.yieldnest_like}

    # Check that management and performance fees return None (not documented)
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Check risk level
    assert vault.get_risk() is None

    # Check lock-up period - ynRWAx has fixed maturity date of 15 Oct 2026
    lock_up = vault.get_estimated_lock_up()
    assert lock_up is not None
    assert isinstance(lock_up, datetime.timedelta)
    assert lock_up.days > 0  # Should be positive until maturity date

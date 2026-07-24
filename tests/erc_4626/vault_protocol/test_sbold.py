"""Test sBOLD vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.sbold.vault import SBOLDVault

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Shared with the other Ethereum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Ethereum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


@flaky.flaky
def test_sbold(
    web3: Web3,
    tmp_path: Path,
):
    """Read sBOLD vault metadata.

    https://etherscan.io/address/0x50bd66d59911f5e086ec87ae43c811e0d059dd11
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x50bd66d59911f5e086ec87ae43c811e0d059dd11",
    )

    assert isinstance(vault, SBOLDVault)
    assert vault.get_protocol_name() == "sBOLD"
    assert vault.features == {ERC4626Feature.sbold_like}

    # Verify vault name and symbol
    assert vault.name == "sBold"
    assert vault.symbol == "sBOLD"

    # Verify the underlying asset is BOLD
    assert vault.denomination_token.symbol == "BOLD"

    # Check fee information
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # Check no lock-up
    assert vault.get_estimated_lock_up().days == 0

    # Check link
    assert vault.get_link() == "https://www.k3.capital/"

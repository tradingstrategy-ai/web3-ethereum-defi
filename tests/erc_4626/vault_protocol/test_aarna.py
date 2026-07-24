"""Test aarnâ vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.aarna.vault import AarnaVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Was forking `latest` (non-reproducible + uncacheable); normalised onto the
    # Ethereum midnight block so it shares the fork with the other Ethereum
    # characterisation tests and is served from the warm RPC cache.
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
def test_aarna(
    web3: Web3,
    tmp_path: Path,
):
    """Read aarnâ vault metadata.

    https://etherscan.io/address/0xb9c1344105faa4681bc7ffd68c5c526da61f2ae8
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xb9c1344105faa4681bc7ffd68c5c526da61f2ae8",
    )

    assert isinstance(vault, AarnaVault)
    assert vault.get_protocol_name() == "aarnâ"
    assert vault.features == {ERC4626Feature.aarna_like}

    # Check vault name
    assert "aarnâ" in vault.name or "atv" in vault.name

    # Fee information not publicly documented
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Risk not yet assessed
    assert vault.get_risk() is None

    # Link should point to the app
    assert vault.get_link() == "https://engine.aarna.ai/"

    # Check maxDeposit and maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0
    assert vault.can_check_redeem() is False

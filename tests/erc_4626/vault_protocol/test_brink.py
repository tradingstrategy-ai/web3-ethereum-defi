"""Brink protocol tests.

Brink is a DeFi protocol providing yield-bearing vaults on Mantle and other chains.

- Homepage: https://brink.money/
- App: https://brink.money/app
- Documentation: https://doc.brink.money/
- Twitter: https://x.com/BrinkDotMoney
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.brink.vault import BrinkVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_MANTLE = os.environ.get("JSON_RPC_MANTLE")

pytestmark = pytest.mark.skipif(JSON_RPC_MANTLE is None, reason="JSON_RPC_MANTLE needed to run these tests")


@pytest.fixture(scope="module")
def anvil_mantle_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_MANTLE, fork_block_number=90_059_927)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_mantle_fork):
    web3 = create_multi_provider_web3(anvil_mantle_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_brink_vault(
    web3: Web3,
    tmp_path: Path,
):
    """Read Brink vault metadata.

    https://mantlescan.xyz/address/0xE12EED61E7cC36E4CF3304B8220b433f1fD6e254
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xE12EED61E7cC36E4CF3304B8220b433f1fD6e254",
    )

    assert isinstance(vault, BrinkVault)
    assert vault.features == {ERC4626Feature.brink_like}
    assert vault.get_protocol_name() == "Brink"

    # Brink vaults don't expose explicit fee getters on-chain
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Verify link generation
    assert vault.get_link() == "https://brink.money/app"

    # Check maxDeposit and maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0
    assert vault.can_check_max_deposit_and_redeem() is False

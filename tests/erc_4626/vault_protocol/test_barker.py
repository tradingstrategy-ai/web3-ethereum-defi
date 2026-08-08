"""Barker H1 hardcoded-vault regression tests."""

import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.barker.vault import BARKER_H1_VAULT_ADDRESS, BarkerVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool

#: Barker H1 was deployed after ``HYPERLIQUID_MIDNIGHT_BLOCK``. Pin this
#: post-deployment block to retain reproducible, archive-backed coverage.
BARKER_H1_FORK_BLOCK = 42_488_003

#: H1 total assets at :data:`BARKER_H1_FORK_BLOCK`, in USDC units.
BARKER_H1_TOTAL_ASSETS = Decimal("10123.705647")

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_HYPERLIQUID is None, reason="JSON_RPC_HYPERLIQUID needed to run these tests"),
    pytest.mark.xdist_group("fork:hyperliquid:barker-h1"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Create a shared, read-only Anvil fork at the Barker H1 test block.

    The deployment post-dates the repository-wide Hyperliquid midnight block,
    so this module has its own fixed block and xdist group.

    :param anvil_fork_pool:
        Session-scoped pool that owns the shared Anvil process.

    :return:
        Web3 connection to the fixed HyperEVM fork.
    """

    return anvil_fork_pool.get_web3(JSON_RPC_HYPERLIQUID, BARKER_H1_FORK_BLOCK)


def test_barker_h1_vault_metadata(web3: Web3) -> None:
    """Classify and read the reviewed Barker H1 vault at a fixed block.

    The regression pins the exact name, share symbol and denomination observed
    at the first reviewed post-deployment test block. It also ensures that the
    adapter does not advertise an unsafe generic transaction manager.

    :param web3:
        Web3 connection to the fixed HyperEVM Anvil fork.
    """

    vault = create_vault_instance_autodetect(web3, BARKER_H1_VAULT_ADDRESS)

    assert isinstance(vault, BarkerVault)
    assert vault.features == {ERC4626Feature.barker_like}
    assert vault.get_protocol_name() == "Barker"
    assert vault.name == "Barker H1 Vault"
    assert vault.symbol == "bh1USDC"
    assert vault.denomination_token.address == "0xb88339CB7199b77E23DB6E890353E22632Ba630f"
    assert vault.fetch_total_assets(BARKER_H1_FORK_BLOCK) == BARKER_H1_TOTAL_ASSETS
    assert vault.get_management_fee(BARKER_H1_FORK_BLOCK) is None
    assert vault.get_performance_fee(BARKER_H1_FORK_BLOCK) is None
    assert vault.get_deposit_manager_capability() is None

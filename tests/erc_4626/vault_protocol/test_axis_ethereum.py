"""Axis StakedUSDx Ethereum V2 vault tests."""

import datetime
import os

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_ETHEREUM_STAKED_USDX_VAULT
from eth_defi.erc_4626.vault_protocol.axis.vault import AxisVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.fee import FeeData, VaultFeeMode

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    # Shares the canonical Ethereum fork with the other read-only
    # characterisation modules and the committed warm RPC cache.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return the shared, fixed-block Ethereum fork used for Axis V2 reads.

    The test is read-only, therefore it can safely share the session-level
    Anvil instance and needs no per-test snapshot restoration.

    :param anvil_fork_pool:
        Session-scoped fixed-block Anvil fork registry.
    :return:
        Web3 client connected to the canonical Ethereum midnight fork.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)


def test_axis_staked_usdx_v2_vault(web3: Web3) -> None:
    """Characterise the reviewed Axis StakedUSDx Ethereum V2 deployment.

    The fixed block post-dates the proxy deployment and proves the chain-aware
    hardcoded classifier selects the Axis adapter for the current V2 vault.

    :param web3:
        Shared Web3 client for the canonical Ethereum midnight fork.
    :return:
        ``None`` after asserting the immutable deployment metadata.
    """
    vault = create_vault_instance_autodetect(web3, AXIS_ETHEREUM_STAKED_USDX_VAULT)

    assert vault.features == {ERC4626Feature.axis_like, ERC4626Feature.erc_7540_like}
    assert isinstance(vault, AxisVault)
    assert vault.get_protocol_name() == "Axis"
    assert vault.name == "Staked USDx"
    assert vault.share_token.symbol == "sUSDx"
    assert vault.denomination_token.address == "0xa1fA7777974312f7d801A8880714a218F76233f8"
    assert vault.get_fee_data() == FeeData(VaultFeeMode.internalised_skimming, 0.0, 0.0, 0.0, 0.0)
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.get_deposit_manager_capability() is None

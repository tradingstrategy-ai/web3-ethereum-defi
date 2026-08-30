"""Axis StakedUSDx vault tests."""

import datetime
import os

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_HARDCODED_LEADS, AXIS_PLASMA_STAKED_USDX_VAULT, AXIS_STAKED_USDX_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.axis.vault import AxisVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import PLASMA_MIDNIGHT_BLOCK
from eth_defi.vault.fee import FeeData, VaultFeeMode

JSON_RPC_PLASMA = os.environ.get("JSON_RPC_PLASMA")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_PLASMA is None, reason="JSON_RPC_PLASMA needed to run these tests"),
    # Shares the canonical Plasma fork with the other read-only characterisation
    # modules so its archive reads warm the same RPC cache in CI.
    pytest.mark.xdist_group("fork:plasma:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Return the shared, fixed-block Plasma fork used for Axis reads.

    The test is read-only, therefore it can safely share the session-level
    Anvil instance and needs no per-test snapshot restoration.

    :param anvil_fork_pool:
        Session-scoped fixed-block Anvil fork registry.
    :return:
        Web3 client connected to the canonical Plasma midnight fork.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_PLASMA, PLASMA_MIDNIGHT_BLOCK)


def test_axis_hardcoded_lead_is_registered() -> None:
    """Ensure the reviewed Axis contract is included in hardcoded discovery.

    Axis uses generic vault interfaces, so a hardcoded lead and address-routed
    feature are required to preserve discovery across historical scans.

    :return:
        ``None`` after checking the static discovery registry.
    """
    assert ("Axis", AXIS_HARDCODED_LEADS) in DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
    assert {(chain_id, address) for chain_id, address, _block, _timestamp in AXIS_HARDCODED_LEADS} == set(AXIS_STAKED_USDX_BY_CHAIN.items())


def test_axis_staked_usdx_vault(web3: Web3) -> None:
    """Characterise the reviewed Axis StakedUSDx Plasma deployment.

    The fixed block post-dates the deployment and proves that the address-only
    classifier selects the Axis adapter while retaining the standard USDx
    denominator and the documented asynchronous redemption limitation.

    :param web3:
        Shared Web3 client for the canonical Plasma midnight fork.
    :return:
        ``None`` after asserting the immutable deployment metadata.
    """
    vault = create_vault_instance_autodetect(web3, AXIS_PLASMA_STAKED_USDX_VAULT)

    assert vault.features == {ERC4626Feature.axis_like, ERC4626Feature.erc_7540_like}
    assert isinstance(vault, AxisVault)
    assert vault.get_protocol_name() == "Axis"
    assert vault.name == "Staked Axis USD"
    assert vault.share_token.symbol == "sUSDx"
    assert vault.denomination_token.address == "0xA1FA77779e6866fa3eF48FC0720657E042158387"
    assert vault.get_fee_data() == FeeData(VaultFeeMode.internalised_skimming, 0.0, 0.0, 0.0, 0.0)
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.get_deposit_manager_capability() is None

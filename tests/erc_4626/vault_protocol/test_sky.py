"""Sky vault detection and withdrawal capacity on the shared Ethereum fork."""

import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.sky.vault import SkyVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultTechnicalRisk

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


def test_sky_vault_dispatch_and_withdrawal_capacity(web3: Web3) -> None:
    """Detect all three Sky products and measure the lending vault at the fork block."""
    staked, savings_usds, savings_dai = [
        create_vault_instance_autodetect(web3, vault_address=address)
        for address in (
            "0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9",
            "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd",
            "0x83f20f44975d03b1b09e64809b757c47f942beea",
        )
    ]
    for vault in (staked, savings_usds, savings_dai):
        assert isinstance(vault, SkyVault)
        assert vault.features == {ERC4626Feature.sky_like}
        assert vault.can_check_redeem() is False

    assert staked.get_protocol_name() == "Sky"
    assert staked.get_management_fee(ETHEREUM_MIDNIGHT_BLOCK) == 0.0
    assert staked.get_performance_fee(ETHEREUM_MIDNIGHT_BLOCK) == 0.0
    assert staked.has_custom_fees() is False
    assert staked.get_link() == "https://sky.money/"
    assert staked.get_risk() == VaultTechnicalRisk.negligible

    limit = staked.fetch_withdrawal_capacity_limit(ETHEREUM_MIDNIGHT_BLOCK)
    assert limit.current_capacity == Decimal("30167514.520225529430700657")
    assert limit.current_tvl == Decimal("186574082.241036731261725321")
    assert limit.capacity_warning is False

    for savings in (savings_usds, savings_dai):
        assert savings.fetch_withdrawal_capacity_observation(ETHEREUM_MIDNIGHT_BLOCK)["status"] == "unsupported"

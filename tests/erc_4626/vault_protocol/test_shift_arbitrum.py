"""Characterise Shift ltLLP account admission on the shared Arbitrum fork."""

import datetime
import os

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.vault.flag import VaultFlag

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Reuse the canonical fixed-block Arbitrum fork for read-only checks.

    :param anvil_fork_pool:
        Shared session-scoped fork pool.
    :return:
        Read-only Arbitrum fork connection.
    """

    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


def test_shift_ltllp_admission_and_settlement(web3: Web3) -> None:
    """Distinguish open investor admission from asynchronous settlement."""

    vault = create_vault_instance_autodetect(web3, "0x956bdd9c18b786b082fd50c52722d254f0cb6964")
    assert isinstance(vault, ShiftVault)
    assert vault.symbol == "ltLLP"
    assert vault.is_whitelisted_deposit() is False
    assert VaultFlag.tokenised_fund not in vault.get_flags()
    # The canonical historical fork predates the current four-day timelock.
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.fetch_deposit_closed_reason() is None
    assert vault.fetch_redemption_closed_reason() is None
    assert vault.get_deposit_manager_capability().as_initial_public_schema() == {"can_deposit": False, "can_redeem": False}

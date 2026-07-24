"""Goat protocol tests"""

import os
from decimal import Decimal
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.goat.vault import GoatVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

#: Fixed Arbitrum block shared with other characterisation tests forking the
#: same point (Lever 1 shared-fork proof-of-concept).
FORK_BLOCK = 392_313_989

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Same xdist_group string as the other Arbitrum@392313989 modules so they
    # share one Anvil process on a single worker under --dist loadgroup.
    pytest.mark.xdist_group("fork:arbitrum:392313989"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Arbitrum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker instead of launching a per-module fork. Read-only
    test, so no snapshot/revert reset is needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, FORK_BLOCK)


@flaky.flaky
def test_goat_protocol(
    web3: Web3,
    tmp_path: Path,
):
    """Bwaaa.

    https://arbiscan.io/address/0x8a1eF3066553275829d1c0F64EE8D5871D5ce9d3#readContract
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x8a1eF3066553275829d1c0F64EE8D5871D5ce9d3",
    )

    assert vault.features == {ERC4626Feature.goat_like}
    assert isinstance(vault, GoatVault)
    assert vault.get_protocol_name() == "Goat Protocol"
    assert vault.name == "Yield Chasing Silo USDC"
    assert vault.denomination_token.address == "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"
    assert vault.denomination_token.symbol == "USDC.e"

    profit, loss = vault.fetch_pnl()
    assert profit == Decimal("5.310608")
    assert loss == 0

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Goat doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False

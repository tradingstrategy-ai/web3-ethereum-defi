"""T3tris deposit manager preflights, ticket status and Anvil settlement.

Runs on the canonical Arbitrum midnight fork with an ordinary account as the
request owner. GuardV0 custody is covered by
``tests/guard/test_guard_simple_vault_t3tris.py``. At this block Gami USDC has
deposits disabled and both whitelists enabled, ``0x394c…`` has only the
deposit whitelist enabled, and First USDC is permissionless.
"""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.t3tris.deposit_redeem import T3TRIS_REQUEST_IN_PROGRESS, T3trisDepositManager
from eth_defi.erc_4626.vault_protocol.t3tris.vault import T3trisVault
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, UnsupportedVaultSimulation, VaultFlowUnavailable, WhitelistingRequired

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM required"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]

FIRST_USDC = "0x98e43a491a464f0886bc5e57207c340bbed0d01f"
GAMI_USDC = "0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f"
WHITELISTED_DEPOSITS = "0x394c4db21b6b429848e123272f206f1f9d8d74b0"


@pytest.fixture(scope="module")
def t3tris_launch(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the canonical Arbitrum fork."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the canonical Arbitrum fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def isolated(t3tris_launch: AnvilLaunch) -> Iterator[None]:
    """Revert every fork mutation."""
    yield from evm_snapshot_revert(t3tris_launch)


def _manager(web3: Web3, address: str) -> T3trisDepositManager:
    """Create the deposit manager for one T3tris vault.

    :param web3: Fork connection.
    :param address: Vault address.
    :return: Manager from the vault adapter.
    """
    manager = T3trisVault(web3, VaultSpec(42161, address)).get_deposit_manager()
    assert isinstance(manager, T3trisDepositManager)
    return manager


def test_t3tris_preflights_refuse_without_broadcast(web3: Web3) -> None:
    """Closed deposits and whitelist exclusions raise typed results.

    1. Refuse deposits to a vault with deposits disabled.
    2. Refuse a deposit and a redemption excluded by the whitelists.
    3. Confirm nothing was broadcast.
    """
    owner = web3.eth.accounts[3]
    start_block = web3.eth.block_number

    # 1. Refuse deposits to a vault with deposits disabled.
    gami = _manager(web3, GAMI_USDC)
    with pytest.raises(VaultFlowUnavailable, match="deposit_closed"):
        gami.create_deposit_request(owner, raw_amount=1, check_enough_token=False)
    assert not gami.can_create_deposit_request(owner)

    # 2. Refuse a deposit and a redemption excluded by the whitelists.
    with pytest.raises(WhitelistingRequired):
        _manager(web3, WHITELISTED_DEPOSITS).create_deposit_request(owner, raw_amount=1, check_enough_token=False)
    with pytest.raises(WhitelistingRequired):
        gami.create_redemption_request(owner, raw_shares=1, check_enough_token=False)
    assert not gami.can_create_redemption_request(owner)
    with pytest.raises(VaultFlowUnavailable, match="insufficient_assets"):
        _manager(web3, FIRST_USDC).create_deposit_request(owner, raw_amount=1)

    # 3. Confirm nothing was broadcast.
    assert web3.eth.block_number == start_block


def test_t3tris_consumed_ticket_stays_consumed(web3: Web3) -> None:
    """A claimed ticket keeps reporting ``none`` after a later request.

    1. Request, settle and claim a deposit.
    2. Make a second deposit request in a later epoch.
    3. The first ticket stays consumed while the second is pending.
    """
    manager = _manager(web3, FIRST_USDC)
    vault = manager.vault
    owner = web3.eth.accounts[3]
    usdc = vault.denomination_token
    raw_amount = usdc.convert_to_raw(Decimal(100))
    fund_erc20_on_anvil(web3, usdc.address, owner, 2 * raw_amount)
    usdc.contract.functions.approve(vault.address, 2 * raw_amount).transact({"from": owner})

    # 1. Request, settle and claim a deposit.
    first = manager.create_deposit_request(owner, raw_amount=raw_amount).broadcast(from_=owner)
    assert manager.can_create_deposit_request(owner) is False
    with pytest.raises(VaultFlowUnavailable, match=T3TRIS_REQUEST_IN_PROGRESS):
        manager.create_deposit_request(owner, raw_amount=raw_amount)
    manager.force_settle(first)
    assert manager.force_settle(first).settlement_required is False
    manager.finish_deposit(first).transact({"from": owner})
    assert manager.get_deposit_request_status(first) is AsyncVaultRequestStatus.none

    # 2. Make a second deposit request in a later epoch.
    second = manager.create_deposit_request(owner, raw_amount=raw_amount).broadcast(from_=owner)
    assert second.request_id == first.request_id + 1

    # 3. The first ticket stays consumed while the second is pending.
    assert manager.get_deposit_request_status(first) is AsyncVaultRequestStatus.none
    assert manager.get_deposit_request_status(second) is AsyncVaultRequestStatus.pending
    assert manager.is_deposit_in_progress(owner)


def test_t3tris_force_settle_refuses_unsupported_options(web3: Web3) -> None:
    """The settlement driver rejects mocks, liquidity bypass and claimed tickets.

    1. Reject ``mock`` and ``ignore_liquidity``.
    2. Reject a ticket that is no longer pending.
    """
    manager = _manager(web3, FIRST_USDC)
    owner = web3.eth.accounts[3]
    usdc = manager.vault.denomination_token
    raw_amount = usdc.convert_to_raw(Decimal(10))
    fund_erc20_on_anvil(web3, usdc.address, owner, raw_amount)
    usdc.contract.functions.approve(manager.vault.address, raw_amount).transact({"from": owner})
    ticket = manager.create_deposit_request(owner, raw_amount=raw_amount).broadcast(from_=owner)

    # 1. Reject mock and ignore_liquidity.
    for kwargs in [{"mock": object()}, {"ignore_liquidity": True}]:
        with pytest.raises(UnsupportedVaultSimulation) as exc_info:
            manager.force_settle(ticket, **kwargs)
        assert exc_info.value.unsupported_reason == "t3tris_anvil_state_required"

    # 2. Reject a ticket that is no longer pending.
    manager.force_settle(ticket)
    manager.finish_deposit(ticket).transact({"from": owner})
    with pytest.raises(UnsupportedVaultSimulation) as exc_info:
        manager.force_settle(ticket)
    assert exc_info.value.unsupported_reason == "t3tris_request_not_pending"

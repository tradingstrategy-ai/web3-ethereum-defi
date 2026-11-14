"""T3tris request, settlement and claim through real Guard and Safe custody.

Uses First USDC on the canonical Arbitrum midnight fork. The vault is in
closed mode, permissionless and accepts deposits at that block. Settlement
impersonates the real oracle owner and curator. Block timestamps are pinned
so that fee accrual, and therefore every asserted amount, is reproducible.
"""

import dataclasses
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from eth_account import Account
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import deploy_safe_trading_strategy_module, setup_guard
from eth_defi.erc_4626.vault_protocol.t3tris.vault import T3trisVault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil, make_anvil_custom_rpc_request, mine
from eth_defi.safe.deployment import deploy_safe
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.erc_7540.deposit_redeem import ERC7540DepositTicket, ERC7540RedemptionTicket
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, VaultFlowUnavailable, VaultRequestInvariantViolation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM required"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]

#: T3tris First USDC: closed mode, permissionless, deposits enabled.
FIRST_USDC = Web3.to_checksum_address("0x98e43a491a464f0886bc5e57207c340bbed0d01f")

#: Pinned block time offset from the fork block, so fee accrual is fixed.
CLOCK_OFFSET_SECONDS = 86_400


@dataclass(slots=True)
class Custody:
    """Guarded custody and its separately authorised asset manager."""

    #: Position owner and payout receiver.
    address: HexAddress
    #: Contract forwarding the asset manager's calls.
    forwarder: Contract
    #: Guard or Safe module enforcing policy.
    guard: Contract
    #: Trading account distinct from governance.
    manager: HexAddress

    def transact(self, web3: Web3, func: ContractFunction) -> HexBytes:
        """Forward a call through the production Guard path.

        :param web3: Fork connection.
        :param func: Bound protocol or token call.
        :return: Successful transaction hash.
        """
        target, data = encode_simple_vault_transaction(func)
        tx = self.forwarder.functions.performCall(target, data).transact({"from": self.manager, "gas": 3_000_000})
        assert_transaction_success_with_explanation(web3, tx)
        return HexBytes(tx)

    def validate(self, func: ContractFunction) -> None:
        """Run the guard's pre-call validation for one call.

        :param func: Bound protocol or token call.
        """
        target, data = encode_simple_vault_transaction(func)
        self.guard.functions.validateCall(self.manager, target, data).call({"from": self.manager})


@pytest.fixture(scope="module")
def t3tris_launch(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the canonical Arbitrum fork."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the canonical Arbitrum fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def isolated(t3tris_launch: AnvilLaunch, web3: Web3) -> Iterator[None]:
    """Revert every mutation and restore Anvil's normal block clock."""
    snapshots = evm_snapshot_revert(t3tris_launch)
    next(snapshots)
    try:
        yield
    finally:
        make_anvil_custom_rpc_request(web3, "anvil_removeBlockTimestampInterval", [])
        next(snapshots, None)


def _pin_clock(web3: Web3) -> None:
    """Make every following block exactly one second after the previous one.

    Fee accrual depends on block time, so the lifecycle starts from a fixed
    timestamp after custody deployment.

    :param web3: Fork connection.
    """
    fork_timestamp = web3.eth.get_block(ARBITRUM_MIDNIGHT_BLOCK)["timestamp"]
    make_anvil_custom_rpc_request(web3, "anvil_setBlockTimestampInterval", [1])
    mine(web3, timestamp=fork_timestamp + CLOCK_OFFSET_SECONDS)


@pytest.fixture
def vault(web3: Web3) -> T3trisVault:
    """First USDC adapter."""
    return T3trisVault(web3, VaultSpec(42161, FIRST_USDC))


def _deploy_simple_custody(web3: Web3) -> Custody:
    """Deploy SimpleVaultV0 and admit First USDC with ``whitelistT3tris()``.

    :param web3: Fork connection.
    :return: Guarded custody.
    """
    governance, manager = web3.eth.accounts[:2]
    simple = deploy_contract(web3, "guard/SimpleVaultV0.json", governance, manager, libraries=GUARD_LIBRARIES)
    assert_transaction_success_with_explanation(web3, simple.functions.initialiseOwnership(governance).transact({"from": governance}))
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple.functions.guard().call())
    assert_transaction_success_with_explanation(web3, guard.functions.whitelistT3tris(FIRST_USDC, "T3tris").transact({"from": governance}))
    return Custody(simple.address, simple, guard, manager)


def _deploy_safe_custody(web3: Web3) -> Custody:
    """Deploy a Safe module and configure it from an address-only descriptor.

    :param web3: Fork connection.
    :return: Guarded Safe custody.
    """
    governance, manager = web3.eth.accounts[:2]
    deployer = HotWallet(Account.create())
    assert_transaction_success_with_explanation(web3, web3.eth.send_transaction({"from": governance, "to": deployer.address, "value": 10**19}))
    safe = deploy_safe(web3, deployer.account, [deployer.address], 1)
    module = deploy_safe_trading_strategy_module(web3, deployer.account, safe, lagoon=False)
    make_anvil_custom_rpc_request(web3, "anvil_impersonateAccount", [deployer.address])
    try:
        setup_guard(
            web3=web3,
            safe=safe,
            deployer=deployer,
            owner=deployer.address,
            asset_managers=[manager],
            vault=None,
            module=module,
            broadcast_func=lambda f: f.transact({"from": deployer.address}),
            erc_4626_vaults=[ERC4626Vault(web3, VaultSpec(42161, FIRST_USDC))],
            hack_sleep=0,
        )
    finally:
        make_anvil_custom_rpc_request(web3, "anvil_stopImpersonatingAccount", [deployer.address])
    assert len(web3.eth.get_code(module.address)) <= 24_576
    return Custody(safe.address, module, module, manager)


@pytest.fixture
def simple_custody(web3: Web3) -> Custody:
    """SimpleVaultV0 custody, deployed inside the test snapshot."""
    return _deploy_simple_custody(web3)


@pytest.mark.parametrize("kind", ["simple", "safe"])
def test_t3tris_guarded_lifecycle(web3: Web3, vault: T3trisVault, kind: str) -> None:
    """Deposit, settle, claim, redeem, settle and claim through custody.

    1. Fund custody and request a deposit through the guard.
    2. Persist the ticket, refuse a second request and settle on the fork.
    3. Claim and analyse the minted shares.
    4. Request a full redemption, settle and claim the assets.
    """
    custody = _deploy_simple_custody(web3) if kind == "simple" else _deploy_safe_custody(web3)
    _pin_clock(web3)
    manager = vault.get_deposit_manager()
    usdc = vault.denomination_token
    raw_amount = usdc.convert_to_raw(Decimal(100))

    # 1. Fund custody and request a deposit through the guard.
    fund_erc20_on_anvil(web3, usdc.address, custody.address, raw_amount)
    assert manager.get_deposit_approval_target() == vault.address
    custody.transact(web3, usdc.contract.functions.approve(manager.get_deposit_approval_target(), raw_amount))
    request = manager.create_deposit_request(custody.address, raw_amount=raw_amount)
    tx = custody.transact(web3, request.funcs[0])

    # 2. Persist the ticket, refuse a second request and settle on the fork.
    ticket = request.parse_deposit_transaction([tx])
    ticket = manager.reconstruct_deposit_ticket(json.loads(json.dumps(manager.serialize_deposit_ticket(ticket))))
    assert ticket.request_id == 7
    assert manager.get_deposit_request_status(ticket) is AsyncVaultRequestStatus.pending
    assert manager.is_deposit_in_progress(custody.address)
    assert usdc.fetch_raw_balance_of(custody.address) == 0
    with pytest.raises(VaultFlowUnavailable, match="request_in_progress"):
        manager.create_deposit_request(custody.address, raw_amount=1, check_enough_token=False)
    result = manager.force_settle(ticket)
    assert result.settlement_required
    assert result.status_after is AsyncVaultRequestStatus.claimable
    assert len(result.transaction_hashes) == 2
    assert manager.can_finish_deposit(ticket)

    # 3. Claim and analyse the minted shares.
    claim = custody.transact(web3, manager.finish_deposit(ticket))
    analysis = manager.analyse_deposit(claim, ticket)
    raw_shares = vault.share_token.fetch_raw_balance_of(custody.address)
    assert raw_shares == 98_471_112
    assert analysis.share_count == vault.share_token.convert_to_decimals(raw_shares)
    assert analysis.denomination_amount == Decimal(100)
    assert analysis.to == custody.address
    assert manager.get_deposit_request_status(ticket) is AsyncVaultRequestStatus.none
    _assert_foreign_ticket_rejected(manager.analyse_deposit, claim, ticket)

    # 4. Request a full redemption, settle and claim the assets.
    request = manager.create_redemption_request(custody.address, raw_shares=raw_shares)
    tx = custody.transact(web3, request.funcs[0])
    redemption = request.parse_redeem_transaction([tx])
    redemption = manager.reconstruct_redemption_ticket(json.loads(json.dumps(manager.serialize_redemption_ticket(redemption))))
    assert manager.get_redemption_request_status(redemption) is AsyncVaultRequestStatus.pending
    assert vault.share_token.fetch_raw_balance_of(custody.address) == 0
    with pytest.raises(VaultFlowUnavailable, match="request_in_progress"):
        manager.create_redemption_request(custody.address, raw_shares=1, check_enough_token=False)
    result = manager.force_settle(redemption)
    assert result.status_after is AsyncVaultRequestStatus.claimable
    claim = custody.transact(web3, manager.finish_redemption(redemption))
    analysis = manager.analyse_redemption(claim, redemption)
    assert usdc.fetch_raw_balance_of(custody.address) == 99_467_796
    assert analysis.denomination_amount == usdc.convert_to_decimals(99_467_796)
    assert analysis.share_count == vault.share_token.convert_to_decimals(raw_shares)
    assert manager.get_redemption_request_status(redemption) is AsyncVaultRequestStatus.none
    assert not manager.is_redemption_in_progress(custody.address)
    _assert_foreign_ticket_rejected(manager.analyse_redemption, claim, redemption)


def _assert_foreign_ticket_rejected(analyse: Callable, claim: HexBytes, ticket: ERC7540DepositTicket | ERC7540RedemptionTicket) -> None:
    """A claim receipt analysed against another request's ticket must halt.

    :param analyse: Manager claim analyser.
    :param claim: Successful claim transaction.
    :param ticket: The ticket the claim belongs to.
    """
    outsider = Web3.to_checksum_address("0x" + "88" * 20)
    for foreign in [dataclasses.replace(ticket, request_id=ticket.request_id + 1), dataclasses.replace(ticket, owner=outsider), dataclasses.replace(ticket, to=outsider)]:
        with pytest.raises(VaultRequestInvariantViolation):
            analyse(claim, foreign)


def test_t3tris_guard_rejects_redirection(web3: Web3, vault: T3trisVault, simple_custody: Custody) -> None:
    """Every address position, pinned flag and unregistered call is checked.

    1. Accept the manager-generated calls.
    2. Reject a substituted address in every validated position.
    3. Reject unsafe mode and Permit2 payloads.
    4. Reject unregistered vault and share-token calls and a foreign approval spender.
    5. Reject malformed calldata and a non-governance whitelist call.
    """
    c = simple_custody
    f = vault.vault_contract.functions
    me = c.address
    outsider = web3.eth.accounts[8]

    # 1. Accept the manager-generated calls.
    for func in [f.requestDeposit(me, False, 1, b""), f.claimDeposit(me), f.requestRedeem(me, me, me, False, 1), f.claimRedeem(me, me, False), vault.denomination_token.contract.functions.approve(vault.address, 1)]:
        c.validate(func)

    # 2. Reject a substituted address in every validated position.
    redirected = {
        "Receiver not whitelisted": [
            f.requestDeposit(outsider, False, 1, b""),
            f.claimDeposit(outsider),
            f.requestRedeem(outsider, me, me, False, 1),
            f.requestRedeem(me, me, outsider, False, 1),
            f.claimRedeem(me, outsider, False),
        ],
        "Owner not whitelisted": [
            f.requestRedeem(me, outsider, me, False, 1),
            f.claimRedeem(outsider, me, False),
        ],
    }
    for message, funcs in redirected.items():
        for func in funcs:
            with pytest.raises(ValueError, match=message):
                c.validate(func)

    # 3. Reject unsafe mode and Permit2 payloads.
    for func in [f.requestDeposit(me, True, 1, b""), f.requestDeposit(me, False, 1, b"\x01"), f.requestRedeem(me, me, me, True, 1), f.claimRedeem(me, me, True)]:
        with pytest.raises(ValueError, match="T3tris unsafe"):
            c.validate(func)

    # 4. Reject unregistered vault calls and a foreign approval spender.
    multicall = get_deployed_contract(web3, "t3tris/Multicall.json", vault.address).functions.multicall([])
    unregistered = [
        f.decreaseDepositRequest(me, False, 1, 0),
        f.decreaseRedeemRequest(me, me, 1),
        f.settle(0, False, b""),
        f.forceClaimDeposit(me),
        f.unsafeRedeem(1, me, me),
        f.unsafeWithdraw(1, me, me),
        f.claimFees(False, 1, 0, b""),
        f.deposit(1, me),
        f.deposit(me, 1, b""),
        f.mint(1, me),
        f.mint(me, 1, b""),
        f.redeem(1, me, me),
        f.withdraw(1, me, me),
        f.transfer(outsider, 1),
        f.transferFrom(me, outsider, 1),
        f.approve(vault.address, 1),
        f.approve(outsider, 1),
        multicall,
    ]
    for func in unregistered:
        with pytest.raises(ValueError, match="not allowed"):
            c.validate(func)
    with pytest.raises(ValueError, match="Approve address not allowed"):
        c.validate(vault.denomination_token.contract.functions.approve(outsider, 1))
    assert not c.guard.functions.isAllowedCallSite(vault.address, Web3.keccak(text="settle(uint8,bool,bytes)")[:4]).call()
    assert c.guard.functions.getInternalVersion().call() == 5

    # 5. Reject malformed calldata and a non-governance whitelist call.
    word = lambda value: value.to_bytes(32, "big")  # noqa: E731
    address_word = word(int(me, 16))
    dirty_address = word(int(me, 16) | (1 << 200))
    malformed = [
        bytes.fromhex("afd933fb") + address_word,
        bytes.fromhex("de56603c") + dirty_address,
        bytes.fromhex("5fb8c2cf") + address_word * 3 + word(2) + word(1),
        bytes.fromhex("9cb4b041") + address_word * 2 + word(2),
        bytes.fromhex("afd933fb") + address_word + word(0) + word(1) + word(0x1000),
    ]
    for data in malformed:
        with pytest.raises((ContractLogicError, ValueError)):
            c.guard.functions.validateCall(c.manager, vault.address, data).call({"from": c.manager})
    with pytest.raises((ContractLogicError, ValueError)):
        c.guard.functions.whitelistT3tris(vault.address, "T3tris").call({"from": c.manager})


def test_t3tris_new_request_auto_claims_to_custody(web3: Web3, vault: T3trisVault, simple_custody: Custody) -> None:
    """A request made while an earlier one is claimable pays the earlier one to custody.

    The manager never builds this sequence, but a guarded asset manager can,
    so the guard's receiver checks must cover the automatic claims.

    1. Request and settle a deposit, then request again without claiming.
    2. Request and settle a partial redemption, then request the rest without claiming.
    """
    c = simple_custody
    _pin_clock(web3)
    manager = vault.get_deposit_manager()
    f = vault.vault_contract.functions
    usdc = vault.denomination_token
    raw_amount = usdc.convert_to_raw(Decimal(100))
    fund_erc20_on_anvil(web3, usdc.address, c.address, 2 * raw_amount)
    c.transact(web3, usdc.contract.functions.approve(vault.address, 2 * raw_amount))

    # 1. Request and settle a deposit, then request again without claiming.
    request = manager.create_deposit_request(c.address, raw_amount=raw_amount)
    ticket = request.parse_deposit_transaction([c.transact(web3, request.funcs[0])])
    manager.force_settle(ticket)
    settled_shares = f.getSettledDepositShares(c.address, ticket.request_id).call()
    assert settled_shares == 98_471_112
    c.transact(web3, f.requestDeposit(c.address, False, raw_amount, b""))
    assert vault.share_token.fetch_raw_balance_of(c.address) == settled_shares
    assert manager.get_deposit_request_status(ticket) is AsyncVaultRequestStatus.none

    # 2. Request and settle a partial redemption, then request the rest without claiming.
    half = settled_shares // 2
    request = manager.create_redemption_request(c.address, raw_shares=half)
    redemption = request.parse_redeem_transaction([c.transact(web3, request.funcs[0])])
    manager.force_settle(redemption)
    # The claim pays previewClaimRedeem(), which rounds the settled assets down.
    claimable_assets, _silo_shares = f.previewClaimRedeem(c.address).call()
    assert claimable_assets == 49_733_897
    c.transact(web3, f.requestRedeem(c.address, c.address, c.address, False, settled_shares - half))
    assert usdc.fetch_raw_balance_of(c.address) == claimable_assets
    assert manager.get_redemption_request_status(redemption) is AsyncVaultRequestStatus.none


def test_t3tris_setup_guard_policy(web3: Web3, simple_custody: Custody) -> None:
    """``setup_guard()`` resolves T3tris descriptors before any broadcast.

    1. A feature-bearing generic descriptor installs ``whitelistT3tris()``.
    2. Conflicting features fail before the first policy transaction.
    """
    c = simple_custody
    owner = web3.eth.accounts[0]
    guard_args = {"web3": web3, "safe": SimpleNamespace(address=c.address), "deployer": HotWallet.from_private_key("0x" + "11" * 32), "owner": owner, "asset_managers": [c.manager], "vault": None, "module": c.guard, "hack_sleep": 0}
    other = Web3.to_checksum_address("0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f")

    # 1. A feature-bearing generic descriptor installs whitelistT3tris().
    descriptor = ERC4626Vault(web3, VaultSpec(42161, other), features={ERC4626Feature.t3tris_like})
    setup_guard(**guard_args, broadcast_func=lambda func: func.transact({"from": owner}), erc_4626_vaults=[descriptor])
    for signature in ["requestDeposit(address,bool,uint256,bytes)", "claimDeposit(address)", "requestRedeem(address,address,address,bool,uint256)", "claimRedeem(address,address,bool)"]:
        assert c.guard.functions.isAllowedCallSite(other, Web3.keccak(text=signature)[:4]).call()
    assert not c.guard.functions.isAllowedCallSite(other, Web3.keccak(text="deposit(uint256,address)")[:4]).call()

    # 2. Conflicting features fail before the first policy transaction.
    conflicting = ERC4626Vault(web3, VaultSpec(42161, other), features={ERC4626Feature.t3tris_like, ERC4626Feature.mellow_like})
    broadcast = Mock()
    with pytest.raises(AssertionError, match="Conflicting guard policy features"):
        setup_guard(**guard_args, broadcast_func=broadcast, erc_4626_vaults=[conflicting])
    broadcast.assert_not_called()

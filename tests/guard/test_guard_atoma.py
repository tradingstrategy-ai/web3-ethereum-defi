"""Current Atoma implementation through real Guard and Safe custody.

The canonical Arbitrum block predates the reviewed upgrade. This explicit pool
key pins its replacement at 519150162; Anvil's persistent RPC cache warms on the
first run. We do not change the shared chain constant or fork at latest.
Funding is a real USDC transfer on the fork. Settlement impersonates the real
operator and affects all requests in the chosen epoch, without changing NAV,
protocol storage or idle assets.
"""

import datetime
import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import pytest
from eth_account import Account
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626 import deposit_probe
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem import AtomaFlowAnalysis
from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_VAULT_ADDRESSES, AtomaVault
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import deploy_safe_trading_strategy_module, setup_guard
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, make_anvil_custom_rpc_request, mine
from eth_defi.safe.deployment import deploy_safe
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, VaultFlowUnavailable

logger = logging.getLogger(__name__)
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
ATOMA_CURRENT_BLOCK = 519_150_162
pytestmark = [pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM required"), pytest.mark.xdist_group("fork:arbitrum:atoma:519150162")]


@dataclass(slots=True)
class Custody:
    """Real custody and its separately authorised asset manager."""

    #: Position owner and payout receiver.
    address: HexAddress
    #: Contract forwarding the authorised call.
    forwarder: Contract
    #: Guard or Safe module enforcing policy.
    guard: Contract
    #: Trading account distinct from governance.
    manager: HexAddress

    def transact(self, web3: Web3, func: ContractFunction) -> HexBytes:
        """Forward a function through the production Guard path.

        :param web3: Fork connection.
        :param func: Bound protocol or token call.
        :return: Successful transaction hash.
        """
        target, data = encode_simple_vault_transaction(func)
        tx = self.forwarder.functions.performCall(target, data).transact({"from": self.manager, "gas": 2_000_000})
        assert_transaction_success_with_explanation(web3, tx)
        return HexBytes(tx)


@pytest.fixture(scope="session")
def atoma_launch(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Use the fixed current-version fork and its warm RPC cache."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ATOMA_CURRENT_BLOCK)


@pytest.fixture(scope="session")
def atoma_web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared current-version fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ATOMA_CURRENT_BLOCK)


@pytest.fixture(scope="session")
def custodies(atoma_web3: Web3) -> dict[str, Custody]:
    """Deploy expensive custody contracts once, before per-test snapshots."""
    w = atoma_web3
    governance, trader = w.eth.accounts[:2]
    simple = deploy_contract(w, "guard/SimpleVaultV0.json", governance, trader, libraries=GUARD_LIBRARIES)
    assert_transaction_success_with_explanation(w, simple.functions.initialiseOwnership(governance).transact({"from": governance}))
    guard = get_deployed_contract(w, "guard/GuardV0.json", simple.functions.guard().call())
    for address in sorted(ATOMA_VAULT_ADDRESSES):
        assert_transaction_success_with_explanation(w, guard.functions.whitelistAtoma(Web3.to_checksum_address(address), "Atoma").transact({"from": governance}))

    deployer = HotWallet(Account.create())
    assert_transaction_success_with_explanation(w, w.eth.send_transaction({"from": governance, "to": deployer.address, "value": 10**19}))
    safe = deploy_safe(w, deployer.account, [deployer.address], 1)
    module = deploy_safe_trading_strategy_module(w, deployer.account, safe, lagoon=False)
    make_anvil_custom_rpc_request(w, "anvil_impersonateAccount", [deployer.address])
    try:
        setup_guard(web3=w, safe=safe, deployer=deployer, owner=deployer.address, asset_managers=[trader], vault=None, module=module, broadcast_func=lambda f: f.transact({"from": deployer.address}), erc_4626_vaults=[AtomaVault(w, VaultSpec(42161, a)) for a in sorted(ATOMA_VAULT_ADDRESSES)], hack_sleep=0)
    finally:
        make_anvil_custom_rpc_request(w, "anvil_stopImpersonatingAccount", [deployer.address])
    assert len(w.eth.get_code(guard.address)) <= 24_576
    assert len(w.eth.get_code(module.address)) <= 24_576
    return {"simple": Custody(simple.address, simple, guard, trader), "safe": Custody(safe.address, module, module, trader)}


@pytest.fixture(autouse=True)
def isolated(atoma_launch: AnvilLaunch, custodies: dict[str, Custody]) -> Iterator[None]:
    """Reset protocol mutations while preserving the session deployments."""
    yield from evm_snapshot_revert(atoma_launch)


def fund(vault: AtomaVault, owner: HexAddress, amount: Decimal = Decimal(100)) -> None:
    """Transfer existing native USDC from the documented fork holder.

    :param vault: Atoma deployment.
    :param owner: Receiving custody address.
    :param amount: Human-readable USDC.
    :return: None.
    """
    w = vault.web3
    whale = Web3.to_checksum_address(USDC_WHALE[42161])
    make_anvil_custom_rpc_request(w, "anvil_impersonateAccount", [whale])
    try:
        make_anvil_custom_rpc_request(w, "anvil_setBalance", [whale, hex(10**18)])
        tx = vault.denomination_token.transfer(owner, amount).transact({"from": whale})
        assert_transaction_success_with_explanation(w, tx)
    finally:
        make_anvil_custom_rpc_request(w, "anvil_stopImpersonatingAccount", [whale])


def deposit(vault: AtomaVault, custody: Custody) -> int:
    """Deposit 100 USDC and verify actual receipt shares.

    :param vault: Current deployment.
    :param custody: Guarded owner.
    :return: Raw shares minted by this deposit.
    """
    manager = vault.get_deposit_manager()
    fund(vault, custody.address)
    raw = vault.denomination_token.convert_to_raw(Decimal(100))
    custody.transact(vault.web3, vault.denomination_token.contract.functions.approve(manager.get_deposit_approval_target(), raw))
    req = manager.create_deposit_request(custody.address, raw_amount=raw)
    tx = custody.transact(vault.web3, req.funcs[0])
    result = manager.analyse_deposit(tx, req.parse_deposit_transaction([tx]))
    assert isinstance(result, AtomaFlowAnalysis)
    assert result.denomination_amount == Decimal(100)
    return vault.share_token.convert_to_raw(result.share_count)


def unlock(vault: AtomaVault, owner: HexAddress) -> None:
    """Advance to the actual deposit lock end on Anvil.

    :param vault: Current deployment.
    :param owner: Locked custody account.
    :return: None.
    """
    end = vault.get_deposit_manager().get_redemption_delay_over(owner)
    assert end is not None
    mine(vault.web3, timestamp=int(end.replace(tzinfo=datetime.UTC).timestamp()))


@pytest.mark.parametrize("address", sorted(ATOMA_VAULT_ADDRESSES))
@pytest.mark.parametrize("kind", ["simple", "safe"])
def test_atoma_guarded_lifecycle(atoma_web3: Web3, custodies: dict[str, Custody], address: HexAddress, kind: str) -> None:
    """Deposit, persist, settle and claim through both real custody contracts."""
    w = atoma_web3
    custody = custodies[kind]
    vault = AtomaVault(w, VaultSpec(42161, address))
    manager = vault.get_deposit_manager()
    shares = deposit(vault, custody)
    assert manager.fetch_unlocked_shares(custody.address) == 0
    with pytest.raises(VaultFlowUnavailable, match="insufficient_unlocked_shares"):
        manager.create_redemption_request(custody.address, raw_shares=shares)
    unlock(vault, custody.address)
    req = manager.create_redemption_request(custody.address, raw_shares=shares)
    tx = custody.transact(w, req.funcs[0])
    ticket = req.parse_redeem_transaction([tx])
    ticket = manager.reconstruct_redemption_ticket(json.loads(json.dumps(manager.serialize_redemption_ticket(ticket))))
    assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.pending
    if kind == "simple":
        assert manager.is_redemption_in_progress(custody.address)
    assert vault.share_token.fetch_raw_balance_of(custody.address) == 0
    f = vault.vault_contract.functions
    reserved_before = f.settledUnclaimedAssets().call()
    result = manager.force_settle(ticket)
    assert result.status_after is AsyncVaultRequestStatus.claimable
    gross, fee, net = manager.fetch_claim_amounts(ticket)
    if address.startswith("0x1c"):
        assert (shares, gross, fee, net) == (92_043_718_995_111, 99_999_999, 499_999, 99_500_000)
    else:
        assert (shares, gross, fee, net) == (90_183_127_089_591, 100_000_000, 500_000, 99_500_000)
    assert f.settledUnclaimedAssets().call() == reserved_before + gross
    assert manager.can_finish_redeem(ticket)
    fees_before = f.accruedWithdrawalFees().call()
    idle_before = vault.denomination_token.fetch_raw_balance_of(vault.address)
    claim = custody.transact(w, manager.finish_redemption(ticket))
    analysis = manager.analyse_redemption(claim, ticket)
    assert isinstance(analysis, AtomaFlowAnalysis)
    assert analysis.is_success()
    assert analysis.denomination_amount == vault.denomination_token.convert_to_decimals(net)
    assert analysis.withdrawal_fee == vault.denomination_token.convert_to_decimals(fee)
    assert vault.denomination_token.fetch_raw_balance_of(custody.address) == net
    assert vault.denomination_token.fetch_raw_balance_of(vault.address) == idle_before - net
    assert f.accruedWithdrawalFees().call() == fees_before + fee
    assert f.settledUnclaimedAssets().call() == reserved_before
    assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.none
    if kind == "simple":
        assert not manager.is_redemption_in_progress(custody.address)
    assert f.userEpochShares(ticket.settlement_epoch, custody.address).call() == 0
    logger.info("Atoma %s %s shares=%s gross=%s fee=%s net=%s", address, kind, shares, gross, fee, net)


def privileged(vault: AtomaVault, role: str, func: ContractFunction) -> HexBytes:
    """Exercise an existing protocol authority on the isolated fork.

    :param vault: Real proxy.
    :param role: Source-defined owner or operator getter.
    :param func: Existing administrative method under test.
    :return: Successful fork transaction.
    """
    w = vault.web3
    actor = getattr(vault.vault_contract.functions, role)().call()
    make_anvil_custom_rpc_request(w, "anvil_impersonateAccount", [actor])
    try:
        make_anvil_custom_rpc_request(w, "anvil_setBalance", [actor, hex(10**18)])
        tx = func.transact({"from": actor, "gas": 1_000_000})
        assert_transaction_success_with_explanation(w, tx)
        return HexBytes(tx)
    finally:
        make_anvil_custom_rpc_request(w, "anvil_stopImpersonatingAccount", [actor])


def test_atoma_partial_locks_paused_exits_and_aggregation(atoma_web3: Web3, custodies: dict[str, Custody]) -> None:
    """Old shares remain unlocked; external same-epoch requests block attribution."""
    c = custodies["safe"]
    v = AtomaVault(atoma_web3, VaultSpec(42161, sorted(ATOMA_VAULT_ADDRESSES)[0]))
    m = v.get_deposit_manager()
    old = deposit(v, c)
    unlock(v, c.address)
    new = deposit(v, c)
    assert m.fetch_unlocked_shares(c.address) == old
    f = v.vault_contract.functions
    privileged(v, "operator", f.pause())
    assert not m.can_create_deposit_request(c.address)
    first = m.create_redemption_request(c.address, raw_shares=old // 2)
    ticket = first.parse_redeem_transaction([c.transact(atoma_web3, first.funcs[0])])
    with pytest.raises(VaultFlowUnavailable, match="epoch_request_already_exists"):
        m.create_redemption_request(c.address, raw_shares=1)
    # A direct governance/external call can enlarge the contract aggregate.
    c.transact(atoma_web3, f.requestWithdrawal(old - old // 2))
    m.force_settle(ticket)
    with pytest.raises(VaultFlowUnavailable, match="reconciliation_required"):
        m.finish_redemption(ticket)
    assert v.share_token.fetch_raw_balance_of(c.address) == new
    # Independent request in a later epoch remains allowed while paused.
    later = m.create_redemption_request(c.address, raw_shares=new)
    next_ticket = later.parse_redeem_transaction([c.transact(atoma_web3, later.funcs[0])])
    assert next_ticket.settlement_epoch != ticket.settlement_epoch
    m.force_settle(next_ticket)
    claim = c.transact(atoma_web3, m.finish_redemption(next_ticket))
    assert isinstance(m.analyse_redemption(claim, next_ticket), AtomaFlowAnalysis)


@pytest.mark.parametrize("zero", [False, True])
def test_atoma_settled_haircuts(atoma_web3: Web3, custodies: dict[str, Custody], zero: bool) -> None:
    """Real source-defined loss reporting changes settled payouts, down to zero."""
    c = custodies["simple"]
    v = AtomaVault(atoma_web3, VaultSpec(42161, sorted(ATOMA_VAULT_ADDRESSES)[0]))
    m = v.get_deposit_manager()
    shares = deposit(v, c)
    unlock(v, c.address)
    req = m.create_redemption_request(c.address, raw_shares=shares)
    ticket = req.parse_redeem_transaction([c.transact(atoma_web3, req.funcs[0])])
    m.force_settle(ticket)
    f = v.vault_contract.functions
    before = m.fetch_claim_amounts(ticket)
    privileged(v, "operator", f.pause())
    # This explicitly controlled loss scenario uses the real owner API. The
    # force_settle helper itself never changes NAV or manufactures liquidity.
    liabilities = f.settledUnclaimedAssets().call()
    privileged(v, "owner", f.resyncTotalAssets(0 if zero else liabilities // 2))
    gross, fee, net = m.fetch_claim_amounts(ticket)
    assert (gross, fee, net) == ((0, 0, 0) if zero else (49_999_999, 249_999, 49_750_000))
    assert gross < before[0]
    claim = c.transact(atoma_web3, m.finish_redemption(ticket))
    analysis = m.analyse_redemption(claim, ticket)
    assert isinstance(analysis, AtomaFlowAnalysis)
    assert analysis.denomination_amount == v.denomination_token.convert_to_decimals(net)
    assert m.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.none


def test_atoma_insufficient_idle(atoma_web3: Web3, custodies: dict[str, Custody]) -> None:
    """Operator settlement does not make an unfunded claim executable."""
    c = custodies["simple"]
    v = AtomaVault(atoma_web3, VaultSpec(42161, sorted(ATOMA_VAULT_ADDRESSES)[0]))
    m = v.get_deposit_manager()
    shares = deposit(v, c)
    unlock(v, c.address)
    f = v.vault_contract.functions
    dest = atoma_web3.eth.accounts[5]
    privileged(v, "owner", f.whitelistCapitalDestination(dest))
    mine(atoma_web3, timestamp=f.capitalDestinationActiveAt(dest).call())
    # Move only the free idle assets through the real owner-authorised route,
    # before this test's claim becomes a reserved liability.
    idle = v.denomination_token.fetch_raw_balance_of(v.address)
    amount = idle - f.settledUnclaimedAssets().call() - f.accruedWithdrawalFees().call()
    privileged(v, "owner", f.capitalWithdraw(dest, amount))
    # Existing reserved assets can still cover a 100 USDC claim. Request a
    # controlled NAV increase before settlement, using the paused owner API.
    privileged(v, "operator", f.pause())
    privileged(v, "owner", f.resyncTotalAssets(f.totalAssets().call() * 10))
    req = m.create_redemption_request(c.address, raw_shares=shares)
    ticket = req.parse_redeem_transaction([c.transact(atoma_web3, req.funcs[0])])
    m.force_settle(ticket)
    assert m.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.claimable
    assert not m.can_finish_redeem(ticket)
    with pytest.raises(VaultFlowUnavailable, match="claim_not_executable"):
        m.finish_redemption(ticket)


def test_atoma_inclusion_epoch_and_schedule_change(atoma_web3: Web3, custodies: dict[str, Custody]) -> None:
    """Mined epochs and scheduled duration changes override preflight timing."""
    c = custodies["simple"]
    v = AtomaVault(atoma_web3, VaultSpec(42161, sorted(ATOMA_VAULT_ADDRESSES)[0]))
    m = v.get_deposit_manager()
    shares = deposit(v, c)
    unlock(v, c.address)
    f = v.vault_contract.functions
    epoch = v.fetch_current_epoch()
    privileged(v, "owner", f.setEpochDuration(86_400))
    req = m.create_redemption_request(c.address, raw_shares=shares)
    mine(atoma_web3, timestamp=int(v.fetch_epoch_end(epoch).replace(tzinfo=datetime.UTC).timestamp()))
    ticket = req.parse_redeem_transaction([c.transact(atoma_web3, req.funcs[0])])
    assert ticket.request_epoch == epoch + 1
    assert ticket.settlement_epoch == epoch + 2
    assert not ticket.reconciliation_required
    assert m.fetch_ticket_epoch_end(ticket) - v.fetch_epoch_end(epoch) == datetime.timedelta(days=2)
    assert v.fetch_user_epoch_shares(c.address, ticket.settlement_epoch) == shares
    m.force_settle(ticket)
    assert v.fetch_epoch(ticket.settlement_epoch)[2]
    fees_before = v.fetch_accrued_withdrawal_fees()
    tx = c.transact(atoma_web3, m.finish_redemption(ticket))
    result = m.analyse_redemption(tx, ticket)
    assert isinstance(result, AtomaFlowAnalysis)
    assert v.fetch_accrued_withdrawal_fees() == fees_before + result.withdrawal_fee
    with pytest.raises(VaultFlowUnavailable, match="claim_not_executable"):
        m.finish_redemption(ticket)


def test_atoma_deposit_probe_route(atoma_web3: Web3, monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual probe configures the narrow policy and reports the share lock."""
    v = AtomaVault(atoma_web3, VaultSpec(42161, sorted(ATOMA_VAULT_ADDRESSES)[0]))
    candidate = deposit_probe.VaultDepositProbeCandidate(v.spec, {"features": {ERC4626Feature.atoma_like}}, v.denomination_token.address)
    broadcasts = []
    original = deposit_probe._broadcast

    def record(wallet: HotWallet, function: ContractFunction, web3: Web3) -> dict:
        """Record selectors while retaining actual guarded fork transactions."""
        broadcasts.append(function.fn_name)
        return original(wallet, function, web3)

    monkeypatch.setattr(deposit_probe, "_broadcast", record)
    monkeypatch.setattr(deposit_probe, "fund_erc20_on_anvil", lambda w, token, owner, raw: fund(v, owner, v.denomination_token.convert_to_decimals(raw)))
    result = deposit_probe.probe_candidate(atoma_web3, candidate, Decimal(100), ATOMA_CURRENT_BLOCK)
    assert result["outcome"] == "success"
    assert result["redemption_status_detail"] == "flow_unavailable"
    assert result["minted_share_amount_raw"] == "92043718995111"
    assert broadcasts.count("whitelistAtoma") == 1
    assert "whitelistERC4626" not in broadcasts

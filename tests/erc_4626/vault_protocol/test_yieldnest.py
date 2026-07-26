"""Test YieldNest ynRWAx vault metadata, deposit and buffer-limited redemption.

Uses the shared Anvil fork pool at the canonical Ethereum midnight block so the
fork is reproducible, shareable and warm-cache friendly (see the module
docstring of :mod:`eth_defi.testing.anvil_fork_pool`). The deposit/redeem body
mutates fork state, so it restores a snapshot between tests.
"""

import datetime
import os
from collections.abc import Iterator
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yieldnest.deposit_redeem import EXCEEDED_MAX_REDEEM_SELECTOR, YieldNestDepositManager
from eth_defi.erc_4626.vault_protocol.yieldnest.vault import YNRWAX_VAULT_ADDRESS, YieldNestVault
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultTechnicalRisk
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = [
    pytest.mark.skipif(not JSON_RPC_ETHEREUM, reason="JSON_RPC_ETHEREUM not set"),
    # Co-locate every same-block Ethereum sharer on one xdist worker.
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]

#: Shares minted for a 10 USDC deposit at ETHEREUM_MIDNIGHT_BLOCK (raw, 18 dp).
YNRWAX_MIDNIGHT_DEPOSIT_RAW_SHARES = 9_209_998_609_480_980_927


@pytest.fixture(scope="module")
def anvil_ethereum_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the canonical fixed Ethereum midnight-block fork, USDC whale unlocked."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_ETHEREUM,
        ETHEREUM_MIDNIGHT_BLOCK,
        unlocked_addresses=[USDC_WHALE[1]],
    )


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Connect to the shared deterministic YieldNest fork."""
    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


@pytest.fixture
def yieldnest_snapshot(anvil_ethereum_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the shared fork after a mutating deposit/redeem test."""
    yield from evm_snapshot_revert(anvil_ethereum_fork)


@flaky.flaky
def test_yieldnest_ynrwax_metadata(web3: Web3) -> None:
    """Read YieldNest ynRWAx vault metadata and capability."""
    vault = create_vault_instance_autodetect(web3, vault_address=YNRWAX_VAULT_ADDRESS)

    assert isinstance(vault, YieldNestVault)
    assert vault.get_protocol_name() == "YieldNest"
    assert vault.features == {ERC4626Feature.yieldnest_like}
    assert vault.is_whitelisted_deposit() is False
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": False,
        "deposit_flow": "synchronous",
        "redemption_unsupported_reason": "maturity_aware_redemption_flow_not_implemented",
    }
    assert isinstance(vault.get_deposit_manager(), YieldNestDepositManager)
    assert vault.vault_contract.events.Deposit is not None
    assert vault.vault_contract.events.Withdraw is not None
    # The full verified implementation ABI carries the buffer-limit error.
    assert "ExceededMaxRedeem" in {e["name"] for e in vault.vault_contract.abi if e.get("type") == "error"}

    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0
    assert vault.get_risk() is VaultTechnicalRisk.low

    lock_up = vault.get_estimated_lock_up()
    assert lock_up is not None
    assert isinstance(lock_up, datetime.timedelta)
    assert lock_up.days > 0

    assert vault.can_check_deposit() is False
    assert vault.can_check_redeem() is False


@flaky.flaky
def test_yieldnest_ynrwax_deposit_and_redemption_preflight(web3: Web3, yieldnest_snapshot: None) -> None:
    """Deposit synchronously, then refuse an over-buffer redemption with a typed error."""
    vault = create_vault_instance_autodetect(web3, vault_address=YNRWAX_VAULT_ADDRESS)
    assert isinstance(vault, YieldNestVault)

    owner = web3.eth.accounts[0]
    deposit_amount = Decimal(10)
    usdc = vault.denomination_token
    funding_hash = usdc.transfer(owner, deposit_amount).transact({"from": USDC_WHALE[1]})
    assert_transaction_success_with_explanation(web3, funding_hash)
    approval_hash = usdc.approve(vault.address, deposit_amount).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)

    manager = vault.get_deposit_manager()
    assert isinstance(manager, YieldNestDepositManager)
    deposit_ticket = manager.create_deposit_request(owner=owner, amount=deposit_amount).broadcast(from_=owner)
    analysis = manager.analyse_deposit(deposit_ticket.tx_hash, deposit_ticket)
    assert analysis.denomination_amount == deposit_amount
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares == YNRWAX_MIDNIGHT_DEPOSIT_RAW_SHARES

    # Redeeming the freshly deposited position exceeds the vault's redemption
    # buffer, so the manager refuses it before broadcast with a typed error
    # carrying the decoded ExceededMaxRedeem selector, instead of leaking the
    # raw 0xb8b8b59c revert.
    max_redeem = vault.vault_contract.functions.maxRedeem(owner).call()
    assert raw_shares > max_redeem
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    error = exc_info.value
    assert error.decoded_error == "ExceededMaxRedeem"
    assert error.error_selector == EXCEEDED_MAX_REDEEM_SELECTOR
    assert error.requested_raw_amount == raw_shares
    assert error.available_raw_amount == max_redeem

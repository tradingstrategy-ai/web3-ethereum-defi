"""Atoma Guard policy on the canonical fork, independent of lifecycle state."""

import os
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_VAULT_ADDRESSES
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = [pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM required"), pytest.mark.xdist_group("fork:arbitrum:midnight")]


@pytest.fixture(scope="module")
def policy_launch(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the canonical fork for generic calldata policy."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def atoma_web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Use the canonical historical fork without execution-manager reads."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def isolated(policy_launch: AnvilLaunch) -> Iterator[None]:
    """Restore policy state for other canonical-fork tests."""
    yield from evm_snapshot_revert(policy_launch)


@pytest.fixture
def policy_guard(atoma_web3: Web3) -> Contract:
    """Configure the real guard with only source-required asset reads."""
    w = atoma_web3
    owner, manager, receiver = w.eth.accounts[:3]
    guard = deploy_contract(w, "guard/GuardV0.json", owner, libraries=GUARD_LIBRARIES)
    for func in [guard.functions.allowSender(manager, "manager"), guard.functions.allowReceiver(receiver, "custody"), guard.functions.whitelistAtoma(Web3.to_checksum_address(sorted(ATOMA_VAULT_ADDRESSES)[0]), "Atoma")]:
        assert_transaction_success_with_explanation(w, func.transact({"from": owner}))
    return guard


def test_atoma_guard_policy(atoma_web3: Web3, policy_guard: Contract) -> None:
    """Guard rejects malformed, redirected, administrative and generic routes."""
    c = SimpleNamespace(guard=policy_guard, manager=atoma_web3.eth.accounts[1], address=atoma_web3.eth.accounts[2])
    v = get_deployed_contract(atoma_web3, "atoma/AtomaVault.json", Web3.to_checksum_address(sorted(ATOMA_VAULT_ADDRESSES)[0]))
    f = v.functions
    outsider = atoma_web3.eth.accounts[8]
    forbidden = [f.redeem(1, c.address, c.address), f.withdraw(1, c.address, c.address), f.approve(outsider, 1), f.transfer(outsider, 1), f.settleEpoch(1), f.collectWithdrawalFees(), f.capitalWithdraw(outsider, 1), f.upgradeToAndCall(outsider, b""), f.deposit(100_000_000, outsider), get_deployed_contract(atoma_web3, "ERC20MockDecimals.json", f.asset().call()).functions.approve(outsider, 1)]
    for func in forbidden:
        target, data = encode_simple_vault_transaction(func)
        with pytest.raises(ValueError, match="not allowed|not whitelisted"):
            c.guard.functions.validateCall(c.manager, target, data).call({"from": c.manager})
    for signature in ["requestWithdrawal(uint256)", "claimWithdrawal(uint256)"]:
        selector = Web3.keccak(text=signature)[:4]
        for payload in [b"", b"\x00" * 31, b"\x00" * 64]:
            with pytest.raises(ValueError, match="Invalid Atoma payload"):
                c.guard.functions.validateCall(c.manager, v.address, selector + payload).call({"from": c.manager})
        with pytest.raises(ValueError, match="Sender not allowed"):
            c.guard.functions.validateCall(outsider, v.address, selector + b"\x00" * 32).call()
        with pytest.raises(ValueError, match="Target not allowed"):
            c.guard.functions.validateCall(c.manager, outsider, selector + b"\x00" * 32).call()
    assert c.guard.functions.isAllowedAsset(v.address).call()
    assert not c.guard.functions.isAllowedCallSite(v.address, Web3.keccak(text="redeem(uint256,address,address)")[:4]).call()

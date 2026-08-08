"""Exercise GuardV0's standard ERC-4626 address policy on the shared fork.

This deliberately does not use the protocol-specific lifecycle profiles in
``test_guard_simple_vault_standard_erc4626.py``.  Those profiles need bespoke
historical blocks with a known deposit-and-redeem state.  GuardV0 rejects the
malicious calldata *before* it calls the vault, so making this policy test pay
for one of those cold historical forks only coupled it to unrelated archive
state and made its CI result depend on upstream ``eth_getStorageAt`` latency.
"""

import os
from collections.abc import Iterator

import pytest
from eth_account import Account
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, set_balance
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

#: A deployed standard ERC-4626 vault at ``ARBITRUM_MIDNIGHT_BLOCK``.
#:
#: ``whitelistERC4626()`` reads only ``asset()`` while configuring GuardV0.
#: The rejected calls below never reach this target: GuardV0 decodes their
#: calldata and rejects the substituted receiver/owner first.  Keep a real
#: vault address nevertheless, because this verifies the production whitelist
#: path rather than relying on an invented contract with a conveniently shaped
#: ABI.
POLICY_TEST_ERC4626_VAULT: HexAddress = "0x030cdecbdca6a34e8de3f49d1798d5f70e3a3414"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Use the canonical group, not a protocol-specific historical group.  CI
    # consequently restores the committed Arbitrum cache and puts this test on
    # the same worker as every other canonical Arbitrum fork consumer.
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the cacheable canonical Arbitrum fork for this policy test.

    The generic address validation does not depend on a vault's liquidity,
    epoch or manager implementation.  A single canonical block therefore
    makes the test share a warm Anvil and avoids an otherwise cold archive
    cache namespace for each protocol profile.
    """
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the pooled canonical Arbitrum fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Revert local GuardV0 deployments without resetting the shared fork."""
    yield from evm_snapshot_revert(anvil_fork)


@pytest.fixture
def guarded_simple_vault(web3: Web3) -> tuple[Contract, HotWallet, Contract]:
    """Deploy and configure the minimum real GuardV0 policy environment.

    Constructing the IERC-4626 binding is an ABI-only operation; it does not
    probe or autodetect the target.  The sole source-chain read in this fixture
    is GuardV0's real ``asset()`` check during whitelisting, which is served by
    the shared canonical fork cache.  This is intentional: it preserves the
    production whitelist behaviour while excluding protocol-manager reads that
    are irrelevant to rejected calldata.
    """
    deployer = HotWallet(Account.create())
    asset_manager = HotWallet(Account.create())
    set_balance(web3, deployer.address, Web3.to_wei(10, "ether"))
    set_balance(web3, asset_manager.address, Web3.to_wei(10, "ether"))
    deployer.sync_nonce(web3)
    asset_manager.sync_nonce(web3)

    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager.address, libraries=GUARD_LIBRARIES)
    initialise_hash = _broadcast(web3, deployer, simple_vault.functions.initialiseOwnership(deployer.address))
    assert_transaction_success_with_explanation(web3, initialise_hash)

    erc4626_vault = get_deployed_erc_4626_contract(web3, POLICY_TEST_ERC4626_VAULT)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = _broadcast(web3, deployer, guard.functions.whitelistERC4626(erc4626_vault.address, "Allow standard ERC-4626 policy target"))
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    return simple_vault, asset_manager, erc4626_vault


def _broadcast(web3: Web3, control: HotWallet, func: ContractFunction, gas: int = 2_000_000) -> HexBytes:
    """Broadcast a generously gas-limited transaction from an Anvil wallet."""
    signed = control.sign_bound_call_with_new_nonce(func, {"gas": gas}, web3=web3, fill_gas_price=True)
    return web3.eth.send_raw_transaction(signed.rawTransaction)


def _assert_guarded_call_rejected(
    web3: Web3,
    simple_vault: Contract,
    control: HotWallet,
    func: ContractFunction,
    expected_error: str,
) -> None:
    """Assert that GuardV0 rejects a substituted standard ERC-4626 address."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = _broadcast(web3, control, simple_vault.functions.performCall(target, call_data))
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_guarded_standard_erc4626_rejects_substituted_addresses(
    web3: Web3,
    guarded_simple_vault: tuple[Contract, HotWallet, Contract],
) -> None:
    """Reject unwhitelisted standard ERC-4626 receivers and share owners."""
    simple_vault, control, erc4626_vault = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])

    # These calls are intentionally never executable deposits or redemptions.
    # GuardV0 must reject their receiver/owner before SimpleVaultV0 reaches the
    # whitelisted target.  Keeping the assertion at this policy boundary means
    # it remains valid for every standard ERC-4626 implementation and can use
    # the canonical warm fork instead of a protocol's liquid historical state.
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        control,
        erc4626_vault.functions.deposit(1, outsider),
        "Receiver not whitelisted",
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        control,
        erc4626_vault.functions.redeem(1, outsider, simple_vault.address),
        "Receiver not whitelisted",
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        control,
        erc4626_vault.functions.redeem(1, simple_vault.address, outsider),
        "Owner not whitelisted",
    )

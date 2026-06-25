"""Guard validation for Lighter L1 deposit/withdraw calls.

Lighter is a zk-rollup perps DEX on Ethereum mainnet (L1), so these tests run
against an Anvil fork of Ethereum mainnet. They exercise the
``GuardV0`` / ``LighterLib`` validation through ``SimpleVaultV0`` and the shared
production whitelisting helper
:py:func:`eth_defi.lighter.deployment.setup_lighter_whitelisting`.

The tests verify:

1. Whitelisting state (isAllowedLighter / isAllowedLighterAssetIndex / receiver).
2. Happy path (``validateCall`` view): approve, deposit, withdraw,
   withdrawPendingBalance to the Safe are accepted.
3. Bad path (``validateCall`` view): deposits/withdrawals to a non-Safe
   receiver, a wrong asset index, the out-of-scope changePubKey selector, a
   random selector, and a wrong target contract are rejected.
4. Execution path: approve + deposit run end-to-end through
   ``SimpleVaultV0.performCall`` and USDC actually leaves the vault.
"""

import os

import pytest
from eth_abi import encode
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT, LIGHTER_USDC_ETHEREUM
from eth_defi.lighter.deployment import LighterDeployment, deploy_lighter_lib, setup_lighter_whitelisting
from eth_defi.lighter.testing import deploy_lighter_simple_vault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, fund_erc20_on_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(
    JSON_RPC_ETHEREUM is None,
    reason="Set JSON_RPC_ETHEREUM env to run Lighter guard tests",
)

#: Non-whitelisted attacker address for negative tests.
ATTACKER = HexAddress(HexStr("0x2222222222222222222222222222222222222222"))

#: changePubKey selector — intentionally NOT whitelisted (out of scope).
SEL_CHANGE_PUBKEY = bytes.fromhex("17010c68")

#: A Lighter account index the freshly-deployed vault does not own. Used to
#: prove the protocol — not the guard — binds ``withdraw`` to the caller's own
#: account (see test_guard_lighter_withdraw_account_index_bound_by_protocol).
FOREIGN_ACCOUNT_INDEX = 1

#: A guard ``validateCall`` revert surfaces either as web3's ContractLogicError
#: or, through the eth_defi multi-provider, as ExtraValueError. Both carry the
#: revert reason string.
REVERT_ERRORS = (ContractLogicError, ExtraValueError)


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum mainnet at a fixed block (Lighter is L1)."""
    # Fixed block where the current ZkLighter implementation (with
    # USDC_ASSET_INDEX) is active. Earlier blocks predate the upgrade.
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        fork_block_number=25_000_000,
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture()
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Web3 connected to the Ethereum mainnet fork."""
    web3 = create_multi_provider_web3(
        anvil_ethereum_fork.json_rpc_url,
        default_http_timeout=(3, 250.0),
    )
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture()
def deployer(web3: Web3) -> str:
    return web3.eth.accounts[0]


@pytest.fixture()
def owner(web3: Web3) -> str:
    return web3.eth.accounts[1]


@pytest.fixture()
def asset_manager(web3: Web3) -> str:
    return web3.eth.accounts[2]


@pytest.fixture()
def safe(web3: Web3) -> str:
    """Stand-in for the asset-managed Safe (the only allowed Lighter owner)."""
    return web3.eth.accounts[3]


@pytest.fixture()
def usdc(web3: Web3) -> TokenDetails:
    return fetch_erc20_details(web3, LIGHTER_USDC_ETHEREUM)


@pytest.fixture()
def zk_lighter(web3: Web3) -> Contract:
    """The live ZkLighter L1 contract (via the mainnet fork)."""
    return get_deployed_contract(web3, "lighter/ZkLighter.json", Web3.to_checksum_address(LIGHTER_L1_CONTRACT))


@pytest.fixture()
def vault(
    web3: Web3,
    deployer: str,
    owner: str,
    asset_manager: str,
    safe: str,
) -> Contract:
    """Deploy SimpleVaultV0 with LighterLib linked, then whitelist Lighter.

    Uses the shared production helper ``setup_lighter_whitelisting`` so the test
    exercises the same code path as the Lagoon deployment flow. The vault holds
    the funds and acts as ``msg.sender`` for ``performCall``.
    """
    lighter_lib = deploy_lighter_lib(web3, deployer)
    vault = deploy_lighter_simple_vault(web3, deployer, asset_manager, owner, lighter_lib)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())

    setup_lighter_whitelisting(
        web3,
        module=guard,
        owner=owner,
        deployment=LighterDeployment.create_ethereum(),
        safe_address=safe,
    )
    return vault


@pytest.fixture()
def guard(web3: Web3, vault: Contract) -> Contract:
    """The GuardV0 owned by the SimpleVaultV0."""
    return get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())


def _calldata(func) -> str:
    """Return ABI-encoded calldata (selector + args) for a bound function."""
    return func._encode_transaction_data()


def test_guard_lighter_validation(
    web3: Web3,
    guard: Contract,
    zk_lighter: Contract,
    usdc: TokenDetails,
    asset_manager: str,
    safe: str,
):
    """Lighter whitelisting + happy-path validation.

    1. Assert whitelisting state (contract / asset-index / receiver / sender).
    2. Assert validateCall accepts approve, deposit, withdraw and
       withdrawPendingBalance to the Safe.
    """
    zk_address = Web3.to_checksum_address(LIGHTER_L1_CONTRACT)
    asset_index = zk_lighter.functions.USDC_ASSET_INDEX().call()

    # 1. Whitelisting state
    assert guard.functions.isAllowedLighter(zk_address).call() is True
    assert guard.functions.isAllowedLighterAssetIndex(asset_index).call() is True
    assert guard.functions.isAllowedReceiver(safe).call() is True
    assert guard.functions.isAllowedSender(asset_manager).call() is True

    # 2. validateCall accepts the in-scope custody calls (view: reverts iff disallowed)
    approve_data = _calldata(usdc.contract.functions.approve(zk_address, 1_000 * 10**6))
    guard.functions.validateCall(asset_manager, usdc.address, approve_data).call()

    deposit_data = _calldata(zk_lighter.functions.deposit(safe, asset_index, 0, 1_000 * 10**6))
    guard.functions.validateCall(asset_manager, zk_address, deposit_data).call()

    withdraw_data = _calldata(zk_lighter.functions.withdraw(0, asset_index, 0, 1_000_000))
    guard.functions.validateCall(asset_manager, zk_address, withdraw_data).call()

    claim_data = _calldata(zk_lighter.functions.withdrawPendingBalance(safe, asset_index, 1_000 * 10**6))
    guard.functions.validateCall(asset_manager, zk_address, claim_data).call()


def test_guard_lighter_blocks_exfiltration(
    web3: Web3,
    guard: Contract,
    zk_lighter: Contract,
    asset_manager: str,
    safe: str,
):
    """Bad-path: the guard rejects fund-egress and out-of-scope calls.

    1. deposit to a non-Safe receiver is rejected.
    2. deposit with a non-whitelisted asset index is rejected.
    3. withdrawPendingBalance to a non-Safe owner is rejected.
    4. changePubKey (out of scope) and a random selector are rejected.
    """
    zk_address = Web3.to_checksum_address(LIGHTER_L1_CONTRACT)
    asset_index = zk_lighter.functions.USDC_ASSET_INDEX().call()
    wrong_asset_index = asset_index + 123

    # 1. Deposit crediting the attacker -> receiver not whitelisted
    bad_to = _calldata(zk_lighter.functions.deposit(ATTACKER, asset_index, 0, 1_000 * 10**6))
    with pytest.raises(REVERT_ERRORS, match="receiver not whitelisted"):
        guard.functions.validateCall(asset_manager, zk_address, bad_to).call()

    # 2. Deposit with a non-whitelisted asset index -> asset not allowed
    bad_asset = _calldata(zk_lighter.functions.deposit(safe, wrong_asset_index, 0, 1_000 * 10**6))
    with pytest.raises(REVERT_ERRORS, match="asset not allowed"):
        guard.functions.validateCall(asset_manager, zk_address, bad_asset).call()

    # 3. Claiming a withdrawal to the attacker -> owner not whitelisted
    bad_owner = _calldata(zk_lighter.functions.withdrawPendingBalance(ATTACKER, asset_index, 1_000 * 10**6))
    with pytest.raises(REVERT_ERRORS, match="owner not whitelisted"):
        guard.functions.validateCall(asset_manager, zk_address, bad_owner).call()

    # 4. changePubKey (out of scope, not whitelisted) -> generic selector rejection
    change_pubkey_data = "0x" + (SEL_CHANGE_PUBKEY + encode(["uint48", "uint8", "bytes"], [0, 0, b"\x00" * 32])).hex()
    with pytest.raises(REVERT_ERRORS):
        guard.functions.validateCall(asset_manager, zk_address, change_pubkey_data).call()

    # A completely unknown selector on the Lighter contract is rejected too.
    random_selector = "0xdeadbeef" + "00" * 32
    with pytest.raises(REVERT_ERRORS):
        guard.functions.validateCall(asset_manager, zk_address, random_selector).call()

    # 5. The same deposit selector against a NON-whitelisted contract address is
    # rejected by the generic call-site / target check (before LighterLib runs).
    other_target = HexAddress(HexStr("0x4444444444444444444444444444444444444444"))
    good_deposit = _calldata(zk_lighter.functions.deposit(safe, asset_index, 0, 1_000 * 10**6))
    with pytest.raises(REVERT_ERRORS):
        guard.functions.validateCall(asset_manager, other_target, good_deposit).call()


def test_guard_lighter_deposit_execution(
    web3: Web3,
    vault: Contract,
    guard: Contract,
    zk_lighter: Contract,
    usdc: TokenDetails,
    asset_manager: str,
    safe: str,
):
    """End-to-end: approve + deposit execute through SimpleVaultV0.performCall.

    Proves the linked LighterLib works in the live execution path (not just the
    validateCall view) and that USDC actually leaves the vault into Lighter.

    1. Fund the vault with USDC (Anvil storage override).
    2. approve(ZkLighter, amount) via performCall.
    3. deposit(safe, USDC_ASSET_INDEX, routeType=0, amount) via performCall.
    4. Assert the vault's USDC balance dropped to zero.
    """
    zk_address = Web3.to_checksum_address(LIGHTER_L1_CONTRACT)
    asset_index = zk_lighter.functions.USDC_ASSET_INDEX().call()
    amount = 1_000 * 10**6

    # 1. Fund the vault (the performCall msg.sender / fund holder) with USDC
    fund_erc20_on_anvil(web3, usdc.address, vault.address, amount)
    assert usdc.contract.functions.balanceOf(vault.address).call() == amount

    # 2. approve(ZkLighter, amount) through the guard
    target, call_data = encode_simple_vault_transaction(usdc.contract.functions.approve(zk_address, amount))
    assert_transaction_success_with_explanation(web3, vault.functions.performCall(target, call_data).transact({"from": asset_manager}))

    # 3. deposit(safe, USDC_ASSET_INDEX, 0, amount) through the guard
    target, call_data = encode_simple_vault_transaction(zk_lighter.functions.deposit(safe, asset_index, 0, amount))
    assert_transaction_success_with_explanation(web3, vault.functions.performCall(target, call_data).transact({"from": asset_manager}))

    # 4. USDC left the vault into the Lighter L1 contract
    assert usdc.contract.functions.balanceOf(vault.address).call() == 0


def test_guard_lighter_withdraw_account_index_bound_by_protocol(
    web3: Web3,
    vault: Contract,
    guard: Contract,
    zk_lighter: Contract,
    asset_manager: str,
):
    """The ZkLighter protocol — not the guard — binds withdraw to ``msg.sender``.

    ``LighterLib`` deliberately leaves ``withdraw``'s ``_accountIndex``
    unchecked (it is not a fund-egress vector: it only moves funds to the
    account owner's pending balance, and the only L1 egress,
    ``withdrawPendingBalance``, is receiver-checked). The verified ``ZkLighter``
    source backs this: ``withdraw`` sets
    ``masterAccountIndex = validateAndGetAccountIndexFromAddress(msg.sender)`` —
    the withdrawal is bound to the *caller's* master account, never to the
    attacker-supplied ``_accountIndex``.

    This test isolates that exact failure mode (not an incidental revert):

    1. The guard ALLOWS withdraw(foreign_index, USDC, Perps, amount) — only the
       asset index is validated, not the account index.
    2. Executed through ``performCall`` with otherwise-valid arguments
       (``routeType=Perps=0``, nonzero in-cap ``baseAmount``), the call reverts
       with the specific ``AdditionalZkLighter_AccountIsNotRegistered()`` custom
       error: the vault is not a registered Lighter account, proving the binding
       is to ``msg.sender`` and a compromised asset manager cannot withdraw a
       foreign account.
    """
    zk_address = Web3.to_checksum_address(LIGHTER_L1_CONTRACT)
    asset_index = zk_lighter.functions.USDC_ASSET_INDEX().call()

    # Otherwise-valid withdraw: routeType=0 is RouteType.Perps, baseAmount=1 is
    # nonzero and within the deposit cap, USDC withdrawals are enabled. The only
    # remaining failure is the msg.sender account binding (checked last).
    withdraw_args = (FOREIGN_ACCOUNT_INDEX, asset_index, 0, 1)

    # 1. The guard permits the call (asset-index check only; account unbound)
    withdraw_data = _calldata(zk_lighter.functions.withdraw(*withdraw_args))
    guard.functions.validateCall(asset_manager, zk_address, withdraw_data).call()

    # 2. The protocol reverts with AccountIsNotRegistered — the withdrawal is
    # bound to msg.sender (the vault), not the supplied _accountIndex. The revert
    # may surface at gas estimation (REVERT_ERRORS) or as a failed receipt.
    not_registered_selector = Web3.keccak(text="AdditionalZkLighter_AccountIsNotRegistered()")[:4].hex().removeprefix("0x").lower()
    target, call_data = encode_simple_vault_transaction(zk_lighter.functions.withdraw(*withdraw_args))
    with pytest.raises((TransactionAssertionError, *REVERT_ERRORS)) as excinfo:
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash)

    assert not_registered_selector in str(excinfo.value).lower(), f"Expected AccountIsNotRegistered ({not_registered_selector}), got: {excinfo.value}"

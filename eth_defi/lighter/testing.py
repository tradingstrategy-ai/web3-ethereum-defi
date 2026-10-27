"""Anvil-fork test helpers for the Lighter guard integration.

This module holds only the test-specific deployment helper; the library
deployment and whitelisting come from :py:mod:`eth_defi.lighter.deployment` so
production and tests share one code path.

See ``eth_defi/lighter/README-lighter-guard.md`` and
``tests/guard/test_guard_lighter_lagoon.py``.
"""

import logging

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.lighter.constants import LIGHTER_L1_CONTRACT
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)

#: Storage slot of ``ZkLighter.addressToAccountIndex`` (``mapping(address => uint48)``).
#:
#: Probed against the mainnet implementation. Used by
#: :py:func:`register_lighter_account_on_anvil` to forge an L1 account
#: registration on a fork.
LIGHTER_ADDRESS_TO_ACCOUNT_INDEX_SLOT = 19

#: Storage slot of ``ZkLighter.pendingAssetBalances``.
#:
#: ``mapping(uint16 assetIndex => mapping(uint48 masterAccountIndex => PendingBalance))``
#: declared in ``ExtendableStorage`` behind a ``uint256[420]`` gap. Probed
#: against the mainnet implementation at ``ETHEREUM_MIDNIGHT_BLOCK`` by
#: writing a candidate slot and reading ``getPendingBalance()`` back. Used by
#: :py:func:`credit_lighter_pending_balance_on_anvil`.
LIGHTER_PENDING_ASSET_BALANCES_SLOT = 498

#: ``PendingBalance.balanceToWithdraw`` occupies the low 128 bits of its slot.
_PENDING_BALANCE_MASK = (1 << 128) - 1


def deploy_lighter_simple_vault(
    web3: Web3,
    deployer: HexAddress | str,
    asset_manager: HexAddress | str,
    owner: HexAddress | str,
    lighter_lib: Contract,
) -> Contract:
    """Deploy ``SimpleVaultV0`` with ``LighterLib`` linked (other libs zeroed).

    Mirrors the Hypercore test fixture: only the library under test gets a real
    address, the rest stay :py:data:`~eth_defi.abi.ZERO_ADDRESS` from
    :py:data:`~eth_defi.deploy.GUARD_LIBRARIES`. The library itself is deployed
    via :py:func:`~eth_defi.lighter.deployment.deploy_lighter_lib`.

    :param web3:
        Web3 connection (Anvil fork).

    :param deployer:
        Deployer address (becomes the initial owner before transfer).

    :param asset_manager:
        Asset manager address (the allowed guard sender).

    :param owner:
        Final guard owner (typically the Safe / governance).

    :param lighter_lib:
        Deployed ``LighterLib`` contract to link.

    :return:
        The deployed ``SimpleVaultV0`` contract.
    """
    libraries = {**GUARD_LIBRARIES, "LighterLib": lighter_lib.address}
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=libraries)
    assert_transaction_success_with_explanation(
        web3,
        vault.functions.initialiseOwnership(owner).transact({"from": deployer}),
    )
    return vault


def register_lighter_account_on_anvil(
    web3: Web3,
    owner: HexAddress | str,
    account_index: int,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> None:
    """Anvil override: forge an L1 Lighter account registration for ``owner``.

    Calls bound to the caller's registered account — ``withdraw`` and
    ``changePubKey`` — read ``masterAccountIndex =
    validateAndGetAccountIndexFromAddress(msg.sender)`` and revert
    ``AccountIsNotRegistered`` if the caller has no account. Registration
    normally happens off-chain (a Safe signature) plus an L2 state update, which
    cannot be reproduced on a fork. This writes ``ZkLighter``'s
    ``addressToAccountIndex[owner] = account_index`` mapping slot directly with
    ``anvil_setStorageAt`` so those calls can be exercised end-to-end.

    :param web3:
        Web3 connection (Anvil fork).

    :param owner:
        Address to register (e.g. the vault's Safe).

    :param account_index:
        Lighter account index to assign (must be non-zero and below
        ``MAX_ACCOUNT_INDEX``).

    :param zk_lighter:
        The ``ZkLighter`` L1 contract address.
    """
    zk_lighter = Web3.to_checksum_address(zk_lighter)
    owner = Web3.to_checksum_address(owner)
    key = Web3.solidity_keccak(["uint256", "uint256"], [int(owner, 16), LIGHTER_ADDRESS_TO_ACCOUNT_INDEX_SLOT])
    web3.provider.make_request(
        "anvil_setStorageAt",
        [zk_lighter, "0x" + key.hex(), "0x" + account_index.to_bytes(32, "big").hex()],
    )
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    assert zk.functions.addressToAccountIndex(owner).call() == account_index, "Failed to forge Lighter account registration"


def credit_lighter_pending_balance_on_anvil(
    web3: Web3,
    owner: HexAddress | str,
    amount_raw: int,
    zk_lighter: HexAddress | str = LIGHTER_L1_CONTRACT,
) -> int:
    """Anvil override: add a claimable Lighter USDC balance for ``owner``.

    A secure withdrawal becomes claimable onchain only when Lighter verifies
    a zk-proof for its batch, which a static fork cannot produce. This writes
    ``pendingAssetBalances[USDC][masterAccountIndex(owner)]`` directly with
    ``anvil_setStorageAt`` so the real ``withdrawPendingBalance(owner, ...)``
    claim, including the guard and Safe module path, can execute end-to-end.

    The owner must already be registered, for example with
    :py:func:`register_lighter_account_on_anvil`. The packed
    ``gasReserveValue`` field is preserved; ``withdrawPendingBalance`` does
    not check it.

    :param web3:
        Web3 connection (Anvil fork).
    :param owner:
        Registered L1 owner, normally the vault Safe.
    :param amount_raw:
        USDC raw amount to add. USDC has a Lighter tick size of one raw unit.
    :param zk_lighter:
        The ``ZkLighter`` L1 contract address.
    :return:
        New pending balance in raw USDC.
    """
    zk_lighter = Web3.to_checksum_address(zk_lighter)
    owner = Web3.to_checksum_address(owner)
    zk = get_deployed_contract(web3, "lighter/ZkLighter.json", zk_lighter)
    master_index = zk.functions.addressToAccountIndex(owner).call()
    assert master_index, f"Lighter owner {owner} is not registered on the fork"
    asset_index = zk.functions.USDC_ASSET_INDEX().call()
    inner = Web3.solidity_keccak(["uint256", "uint256"], [asset_index, LIGHTER_PENDING_ASSET_BALANCES_SLOT])
    slot = Web3.solidity_keccak(["uint256", "bytes32"], [master_index, inner])
    current_word = int.from_bytes(web3.eth.get_storage_at(zk_lighter, slot), "big")
    new_balance = (current_word & _PENDING_BALANCE_MASK) + amount_raw
    assert new_balance <= _PENDING_BALANCE_MASK, "Lighter pending balance overflow"
    new_word = (current_word & ~_PENDING_BALANCE_MASK) | new_balance
    web3.provider.make_request(
        "anvil_setStorageAt",
        [zk_lighter, "0x" + slot.hex(), "0x" + new_word.to_bytes(32, "big").hex()],
    )
    assert zk.functions.getPendingBalance(owner, asset_index).call() == new_balance, "Failed to forge Lighter pending balance"
    return new_balance

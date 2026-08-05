"""Exercise the dedicated Hypercore vault policy on an isolated local Anvil.

These tests deploy the real ``SimpleVaultV0`` and its ``GuardV0`` together with
the compiled ``HypercoreVaultLib`` and ``MockCoreWriter``. They avoid a
mainnet fork so the flag's access control and calldata policy run on every CI
build without relying on an archive RPC provider.
"""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from eth_abi import encode
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.hyperliquid.core_writer import ACTION_VAULT_TRANSFER, CORE_DEPOSIT_WALLET, CORE_WRITER_ADDRESS, encode_vault_deposit
from eth_defi.hyperliquid.testing import deploy_mock_core_writer
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation

#: An arbitrary native vault that is deliberately never allowlisted.
UNLISTED_HYPERCORE_VAULT = HexAddress("0x2222222222222222222222222222222222222222")


@dataclass(slots=True)
class GuardedHypercoreDeployment:
    """Capture one isolated GuardV0 deployment and its test actors.

    Each test receives fresh contracts from the local Anvil snapshot so policy
    mutations cannot leak between security assertions.
    """

    #: Deployed caller that owns assets and delegates validation to GuardV0.
    simple_vault: Contract

    #: GuardV0 created by :attr:`simple_vault`.
    guard: Contract

    #: Mock CoreWriter installed at the HyperEVM system address.
    mock_core_writer: Contract

    #: Address authorised to configure the guard.
    owner: HexAddress

    #: Address authorised to execute guarded calls.
    asset_manager: HexAddress

    #: Unauthorised address used for access-control assertions.
    outsider: HexAddress


@pytest.fixture(scope="module")
def anvil() -> Iterator[AnvilLaunch]:
    """Launch the isolated local EVM used by the Hypercore flag tests.

    The backend has no upstream fork, making these security assertions fast
    and deterministic in CI.

    :return:
        Iterator yielding the running Anvil process.
    """
    launch = launch_anvil()
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil: AnvilLaunch) -> Web3:
    """Connect Web3 to the isolated local Anvil process.

    :param anvil:
        Running local Anvil process.
    :return:
        Web3 connection for deploying and exercising the real guard bytecode.
    """
    return Web3(HTTPProvider(anvil.json_rpc_url))


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil: AnvilLaunch) -> Iterator[None]:
    """Restore the local EVM after each security assertion.

    :param anvil:
        Running local Anvil process.
    :return:
        Snapshot-revert iterator yielding once to the test.
    """
    yield from evm_snapshot_revert(anvil)


@pytest.fixture
def guarded_hypercore(web3: Web3) -> GuardedHypercoreDeployment:
    """Deploy and configure a real GuardV0 with Hypercore call sites enabled.

    No native vault address is allowlisted. Individual tests decide whether to
    enable the dedicated dynamic-vault flag or the unrelated generic asset
    flag before submitting a CoreWriter action.

    :param web3:
        Local Anvil connection.
    :return:
        Fresh guard deployment, CoreWriter mock and test actors.
    """
    deployer = HexAddress(web3.eth.accounts[0])
    owner = HexAddress(web3.eth.accounts[1])
    asset_manager = HexAddress(web3.eth.accounts[2])
    outsider = HexAddress(web3.eth.accounts[3])

    mock_core_writer = deploy_mock_core_writer(web3)
    hypercore_vault_lib = deploy_contract(web3, "guard/HypercoreVaultLib.json", deployer)
    libraries = {**GUARD_LIBRARIES, "HypercoreVaultLib": hypercore_vault_lib.address}
    simple_vault = deploy_contract(
        web3,
        "guard/SimpleVaultV0.json",
        deployer,
        asset_manager,
        libraries=libraries,
    )
    assert_transaction_success_with_explanation(
        web3,
        simple_vault.functions.initialiseOwnership(owner).transact({"from": deployer}),
    )

    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    assert_transaction_success_with_explanation(
        web3,
        guard.functions.whitelistCoreWriter(
            Web3.to_checksum_address(CORE_WRITER_ADDRESS),
            Web3.to_checksum_address(CORE_DEPOSIT_WALLET[999]),
            "Enable Hypercore calls",
        ).transact({"from": owner}),
    )

    return GuardedHypercoreDeployment(
        simple_vault=simple_vault,
        guard=guard,
        mock_core_writer=mock_core_writer,
        owner=owner,
        asset_manager=asset_manager,
        outsider=outsider,
    )


def _perform_core_writer_action(
    deployment: GuardedHypercoreDeployment,
    raw_action: bytes,
) -> HexBytes:
    """Submit one encoded CoreWriter action through SimpleVaultV0 and GuardV0.

    An explicit gas limit ensures rejected calls are mined with receipt status
    zero, allowing the shared transaction assertion helper to explain them.

    :param deployment:
        Guarded local Hypercore deployment.
    :param raw_action:
        Hypercore CoreWriter action payload including version and action ID.
    :return:
        Submitted transaction hash.
    """
    fn_call = deployment.mock_core_writer.functions.sendRawAction(raw_action)
    target, call_data = encode_simple_vault_transaction(fn_call)
    return HexBytes(
        deployment.simple_vault.functions.performCall(target, call_data).transact(
            {"from": deployment.asset_manager, "gas": 1_000_000},
        )
    )


def test_any_hypercore_vault_flag_defaults_false_and_is_owner_controlled(
    web3: Web3,
    guarded_hypercore: GuardedHypercoreDeployment,
) -> None:
    """Check the new storage flag's default, ownership and configuration event.

    An unauthorised account cannot change the variable. The owner can enable
    it, after which both the public variable and effective vault-policy view
    report the dynamic policy and the audit event records the change.

    :param web3:
        Local Anvil connection.
    :param guarded_hypercore:
        Fresh GuardV0 deployment with Hypercore call sites configured.
    """
    guard = guarded_hypercore.guard
    assert guard.functions.anyHypercoreVault().call() is False

    tx_hash = guard.functions.setAnyHypercoreVaultAllowed(True, "Unauthorised change").transact(
        {"from": guarded_hypercore.outsider, "gas": 1_000_000},
    )
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)
    assert guard.functions.anyHypercoreVault().call() is False

    tx_hash = guard.functions.setAnyHypercoreVaultAllowed(True, "Enable dynamic Hypercore vaults").transact(
        {"from": guarded_hypercore.owner},
    )
    receipt = assert_transaction_success_with_explanation(web3, tx_hash)

    events = guard.events.AnyHypercoreVaultSet().process_receipt(receipt)
    assert len(events) == 1
    assert events[0]["args"] == {
        "value": True,
        "notes": "Enable dynamic Hypercore vaults",
    }
    assert guard.functions.anyHypercoreVault().call() is True


def test_any_hypercore_vault_allows_unlisted_vault(
    web3: Web3,
    guarded_hypercore: GuardedHypercoreDeployment,
) -> None:
    """Allow an unlisted action-2 vault only after enabling the dedicated flag.

    The successful action traverses the real SimpleVaultV0 and GuardV0
    dispatcher before reaching MockCoreWriter at its system address.

    :param web3:
        Local Anvil connection.
    :param guarded_hypercore:
        Fresh GuardV0 deployment with Hypercore call sites configured.
    """
    guard = guarded_hypercore.guard
    assert guard.functions.anyAsset().call() is False
    assert_transaction_success_with_explanation(
        web3,
        guard.functions.setAnyHypercoreVaultAllowed(True, "Allow native vault universe").transact(
            {"from": guarded_hypercore.owner},
        ),
    )

    tx_hash = _perform_core_writer_action(
        guarded_hypercore,
        encode_vault_deposit(UNLISTED_HYPERCORE_VAULT, 1_000 * 10**6),
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert guarded_hypercore.mock_core_writer.functions.getActionCount().call() == 1
    sender, version, action_id, _params = guarded_hypercore.mock_core_writer.functions.getAction(0).call()
    assert sender == guarded_hypercore.simple_vault.address
    assert version == 1
    assert action_id == ACTION_VAULT_TRANSFER


def test_any_asset_does_not_allow_unlisted_hypercore_vault(
    web3: Web3,
    guarded_hypercore: GuardedHypercoreDeployment,
) -> None:
    """Keep generic ``anyAsset`` outside the Hypercore vault-address policy.

    This is the regression boundary for the PR: enabling the broader ERC-20
    escape hatch must not change the new variable or permit an unlisted
    CoreWriter action-2 destination.

    :param web3:
        Local Anvil connection.
    :param guarded_hypercore:
        Fresh GuardV0 deployment with Hypercore call sites configured.
    """
    guard = guarded_hypercore.guard
    assert_transaction_success_with_explanation(
        web3,
        guard.functions.setAnyAssetAllowed(True, "Exercise generic asset policy").transact(
            {"from": guarded_hypercore.owner},
        ),
    )
    assert guard.functions.anyAsset().call() is True
    assert guard.functions.anyHypercoreVault().call() is False

    tx_hash = _perform_core_writer_action(
        guarded_hypercore,
        encode_vault_deposit(UNLISTED_HYPERCORE_VAULT, 1_000 * 10**6),
    )
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)
    assert guarded_hypercore.mock_core_writer.functions.getActionCount().call() == 0


def test_any_hypercore_vault_does_not_allow_other_core_writer_actions(
    web3: Web3,
    guarded_hypercore: GuardedHypercoreDeployment,
) -> None:
    """Keep non-vault CoreWriter action IDs rejected by the dedicated flag.

    ``anyHypercoreVault`` bypasses only the destination list inside action 2;
    it cannot turn an otherwise unsupported CoreWriter action into an allowed
    call.

    :param web3:
        Local Anvil connection.
    :param guarded_hypercore:
        Fresh GuardV0 deployment with Hypercore call sites configured.
    """
    assert_transaction_success_with_explanation(
        web3,
        guarded_hypercore.guard.functions.setAnyHypercoreVaultAllowed(True, "Allow native vault universe").transact(
            {"from": guarded_hypercore.owner},
        ),
    )

    unsupported_action = b"\x01" + (1).to_bytes(3, "big") + encode(["uint64"], [1_000])
    tx_hash = _perform_core_writer_action(guarded_hypercore, unsupported_action)
    with pytest.raises(TransactionAssertionError):
        assert_transaction_success_with_explanation(web3, tx_hash)
    assert guarded_hypercore.mock_core_writer.functions.getActionCount().call() == 0

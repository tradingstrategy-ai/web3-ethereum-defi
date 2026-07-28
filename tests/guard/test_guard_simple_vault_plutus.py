"""Exercise Plutus Hedge ERC-7540 request validation through GuardV0."""

import os
from collections.abc import Iterator

import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusVault
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

#: Plutus Hedge's reviewed asynchronous ERC-7540 redemption deployment.
PLUTUS_HEDGE_VAULT: HexAddress = "0x58BfC95a864e18E8F3041D2FCD3418f48393fE6A"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def plutus_anvil_fork(anvil_fork_pool: AnvilForkPool):
    """Return the shared Arbitrum fork for ERC-7540 guard tests."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared Arbitrum midnight fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(plutus_anvil_fork) -> Iterator[None]:
    """Revert every guard configuration and deployment before the next test."""
    yield from evm_snapshot_revert(plutus_anvil_fork)


@pytest.fixture
def plutus_vault(web3: Web3) -> PlutusVault:
    """Open Plutus Hedge through its reviewed asynchronous adapter."""
    vault = create_vault_instance_autodetect(web3, PLUTUS_HEDGE_VAULT)
    assert isinstance(vault, PlutusVault)
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, plutus_vault: PlutusVault) -> tuple[Contract, HexAddress]:
    """Deploy SimpleVaultV0 and configure Plutus' ERC-7540 target."""
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = guard.functions.whitelistERC4626(plutus_vault.address, "Allow Plutus ERC-7540").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    return simple_vault, asset_manager


@pytest.mark.parametrize(
    "case",
    [
        ("outsider", "vault", "Receiver not whitelisted"),
        ("vault", "outsider", "Owner not whitelisted"),
    ],
)
def test_guarded_plutus_rejects_unwhitelisted_erc7540_addresses(
    web3: Web3,
    plutus_vault: PlutusVault,
    guarded_simple_vault: tuple[Contract, HexAddress],
    case: tuple[str, str, str],
) -> None:
    """GuardV0 rejects every sensitive ERC-7540 request address mutation."""
    simple_vault, asset_manager = guarded_simple_vault
    controller, owner, expected_error = case
    outsider = HexAddress(web3.eth.accounts[3])
    controller_address = outsider if controller == "outsider" else simple_vault.address
    owner_address = outsider if owner == "outsider" else simple_vault.address
    target, call_data = encode_simple_vault_transaction(plutus_vault.vault_contract.functions.requestRedeem(1, controller_address, owner_address))
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager, "gas": 1_000_000})
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)

"""Test contract deployment with Forge.

To prepare test run:

.. code-block:: shell

    make guard in-house

"""

import os.path
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.deploy import build_guard_forge_libraries
from eth_defi.foundry.forge import deploy_contract_with_forge
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.abi import ZERO_ADDRESS


@pytest.fixture(scope="module")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""
    anvil = launch_anvil(
        code_size_limit=99_999,
    )
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def web3(anvil: AnvilLaunch):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil.json_rpc_url))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    return web3


@pytest.fixture
def deployer(web3) -> LocalAccount:
    """Create a priavte key with balance."""
    _deployer = web3.eth.accounts[0]
    account: LocalAccount = Account.create()
    stash = web3.eth.get_balance(_deployer)
    tx_hash = web3.eth.send_transaction({"from": _deployer, "to": account.address, "value": stash // 2})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return account


@pytest.fixture()
def guard_project_folder() -> Path:
    """Location of test Foundry folder."""
    p = (Path(os.path.dirname(__file__)) / ".." / ".." / "contracts" / "guard").resolve()
    assert p.exists(), f"Does not exist: {p}"
    return p


@pytest.fixture()
def inhouse_project_folder() -> Path:
    """Location of test Foundry folder."""
    p = (Path(os.path.dirname(__file__)) / ".." / ".." / "contracts" / "in-house").resolve()
    assert p.exists(), f"Does not exist: {p}"
    return p


def test_deploy_contract_with_forge_no_constructor_args(
    web3,
    guard_project_folder,
    deployer: LocalAccount,
):
    """Deploy a contract using forge command."""

    hot_wallet = HotWallet(deployer)
    hot_wallet.sync_nonce(web3)

    contract, tx_hash = deploy_contract_with_forge(
        web3,
        guard_project_folder,
        Path("GuardV0.sol"),
        "GuardV0",
        hot_wallet,
        constructor_args=[],
        wait_for_block_confirmations=0,
        forge_libraries=build_guard_forge_libraries(),
    )

    assert isinstance(tx_hash, HexBytes)
    assert len(tx_hash) == 32
    assert contract.functions.getInternalVersion().call() > 0


def test_deploy_contract_with_forge_with_constructor_args(
    web3,
    inhouse_project_folder,
    deployer: LocalAccount,
):
    """Deploy a contract using forge command."""
    hot_wallet = HotWallet(deployer)
    hot_wallet.sync_nonce(web3)

    contract, _ = deploy_contract_with_forge(
        web3,
        inhouse_project_folder,
        Path("GuardedGenericAdapter.sol"),
        "GuardedGenericAdapter",
        hot_wallet,
        constructor_args=[ZERO_ADDRESS, ZERO_ADDRESS],
        wait_for_block_confirmations=0,
    )

    assert contract.functions.vault().call() == ZERO_ADDRESS

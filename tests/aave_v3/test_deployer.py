"""Aave v3 deployer tests."""
import pytest
from _pytest.fixtures import FixtureRequest
from web3 import Web3, HTTPProvider

from eth_defi.aave_v3.deployer import AaveDeployer
from eth_defi.anvil import AnvilLaunch, launch_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.trace import assert_transaction_success_with_explanation


@pytest.fixture()
def anvil(request: FixtureRequest) -> AnvilLaunch:
    """Launch Anvil for the test backend."""

    # London hardfork will enable EIP-1559 style gas fees
    anvil = launch_anvil(
        hardfork="london",
        gas_limit=15_000_000,  # Max 5M gas per block, or per transaction in test automining
        port=8545,
    )
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.
    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(HTTPProvider(anvil.json_rpc_url))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    return web3


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account"""
    return web3.eth.accounts[0]


def test_install_aave_deployer(aave_deployer: AaveDeployer):
    """Check Aave deployer git clone and npm install works"""
    assert aave_deployer.is_installed()


def test_deploy_aave(
    aave_deployer: AaveDeployer,
    anvil,
):
    """Deploy Aave against local and check it's there."""
    aave_deployer.deploy_local(echo=True)


def test_deploy_aave_pool(
        aave_deployer: AaveDeployer,
        anvil,
        web3: Web3,
        deployer,
):
    """Deploy Aave against local and check it's there."""
    aave_deployer.deploy_local()
    Pool = aave_deployer.get_contract(web3, "core-v3/contracts/protocol/pool/Pool.sol/Pool.json")
    assert Pool is not None

    pool_addresses_provider_address = aave_deployer.get_contract_address("PoolAddressProvider")

    tx_hash = Pool.constructor(pool_addresses_provider_address).transact(
        {
            "from": deployer,
            "gas": 10_000_000,
        }
    )

    tx_receipt = assert_transaction_success_with_explanation(web3, tx_hash)

    pool = Pool(
        address=tx_receipt["contractAddress"],
    )
    revision = pool.functions.getRevision().call()
    assert revision == 1
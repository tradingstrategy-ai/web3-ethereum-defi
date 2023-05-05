"""Aave v3 deployer tests."""
import pytest
from _pytest.fixtures import FixtureRequest

from eth_defi.aave_v3.deployer import AaveDeployer
from eth_defi.anvil import AnvilLaunch, launch_anvil


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


def test_install_aave_deployer(aave_deployer: AaveDeployer):
    """Check Aave deployer git clone and npm install works"""
    assert aave_deployer.is_installed()


def test_deploy_aave(
    aave_deployer: AaveDeployer,
    anvil,
):
    """Deploy Aave against local and check it's there."""
    aave_deployer.deploy_local()


def test_deploy_aave_pool(
        aave_deployer: AaveDeployer,
        anvil,
):
    """Deploy Aave against local and check it's there."""
    aave_deployer.deploy_local()

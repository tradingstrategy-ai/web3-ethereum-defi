"""Test 1delta deployment."""

import pytest

from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.one_delta.deployment import OneDeltaDeployment, deploy_1delta
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, deploy_uniswap_v2_like
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment, deploy_uniswap_v3


@pytest.fixture
def aave_v3_deployment(web3, aave_deployment):
    pool = aave_deployment.get_contract_at_address(web3, "Pool.json", "PoolProxy")

    data_provider = aave_deployment.get_contract_at_address(web3, "AaveProtocolDataProvider.json", "PoolDataProvider")

    oracle = aave_deployment.get_contract_at_address(web3, "AaveOracle.json", "AaveOracle")

    return AaveV3Deployment(
        web3=web3,
        pool=pool,
        data_provider=data_provider,
        oracle=oracle,
    )


@pytest.fixture()
def uniswap_v2(web3, deployer) -> UniswapV2Deployment:
    """Uniswap v2 deployment."""
    return deploy_uniswap_v2_like(web3, deployer, give_weth=None)


@pytest.fixture()
def uniswap_v3(web3, deployer, weth) -> UniswapV3Deployment:
    """Uniswap v3 deployment."""
    return deploy_uniswap_v3(web3, deployer, weth=weth, give_weth=None)


@pytest.fixture
def one_delta_deployment(web3, deployer, aave_v3_deployment, uniswap_v3, uniswap_v2, weth):
    return deploy_1delta(
        web3,
        deployer,
        uniswap_v2,
        uniswap_v3,
        aave_v3_deployment,
        weth,
    )


def test_1delta_deployment(one_delta_deployment: OneDeltaDeployment):
    """Test 1delta deployment."""
    assert one_delta_deployment.flash_aggregator.address

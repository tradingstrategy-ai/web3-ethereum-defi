"""Deploy Enzyme protcol v4.

Based on https://github.com/enzymefinance/protocol/blob/v4/packages/protocol/tests/release/e2e/FundManagementWalkthrough.test.ts
"""
from _decimal import Decimal

import pytest
from eth_abi import encode
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.enzyme.deployment import EnzymeDeployment
from eth_defi.enzyme.utils import convert_rate_to_scaled_per_second_rate
from eth_defi.token import create_token



def test_deploy_enzyme(web3, deployer, weth, mln):
    """Test Enzyme deployment."""

    test_environment = EnzymeDeployment.deploy_test_environment(
        web3,
        deployer,
        mln,
        weth
    )

    assert test_environment.functions.getMlnToken().call() == mln.address
    assert test_environment.functions.getWethToken().call() == weth.address
    assert test_environment.functions.getWrappedNativeToken().call() == weth.address

    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        test_environment,
    )


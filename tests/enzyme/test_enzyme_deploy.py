"""Deploy Enzyme protcol v4.

Based on https://github.com/enzymefinance/protocol/blob/v4/packages/protocol/tests/release/e2e/FundManagementWalkthrough.test.ts
"""
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset


def test_deploy_enzyme(
        web3: Web3,
        deployer: HexAddress,
        user_1: HexAddress,
        user_2: HexAddress,
        weth: Contract,
        mln: Contract,
        usdc: Contract,
        usdc_usd_mock_chainlink_aggregator: Contract,
):
    """Test Enzyme deployment."""

    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        mln,
        weth,
    )

    # Create a vault for user 1
    # where we nominate everything in USDC

    deployment.add_primitive(
        usdc,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    comptroller_proxy, deployment.create_new_vault(
        user_1,
        usdc,
    )

    # User 2 buys into the vault

    usdc.functions.transfer(user_2, 500*10**6).transact({"from": deployer})

    comptroller =







"""Test Enzyme policy API.

"""
import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.policy import get_vault_policies, create_safe_default_policy_configuration_for_generic_adapter
from eth_defi.enzyme.vault import Vault
from eth_defi.trace import assert_transaction_success_with_explanation


@pytest.fixture
def deployment(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    weth: Contract,
    mln: Contract,
    usdc: Contract,
    weth_usd_mock_chainlink_aggregator: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
) -> EnzymeDeployment:
    """Create Enzyme deployment that supports WETH and USDC tokens"""

    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        mln,
        weth,
    )

    tx_hash = deployment.add_primitive(
        usdc,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = deployment.add_primitive(
        weth,
        weth_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Set ethUsdAggregator needed for Enzyme's internal functionality
    tx_hash = deployment.contracts.value_interpreter.functions.setEthUsdAggregator(weth_usd_mock_chainlink_aggregator.address).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return deployment


def test_fetch_policies_empty(
    web3: Web3,
    deployment: EnzymeDeployment,
    user_1,
    usdc,
):
    """By default vault does not have any policies set."""

    comptroller_contract, vault_contract = deployment.create_new_vault(user_1, usdc, fund_name="Cow says Moo", fund_symbol="MOO")
    vault = Vault(vault_contract, comptroller_contract, deployment)

    policies = list(get_vault_policies(vault))
    assert len(policies) == 0


def test_fetch_default_safe_policies(
    web3: Web3,
    deployment: EnzymeDeployment,
    user_1,
    usdc,
):
    """Deploy a vault with the default safe policies."""

    policy = create_safe_default_policy_configuration_for_generic_adapter(deployment)

    comptroller_contract, vault_contract = deployment.create_new_vault(
        user_1,
        usdc,
        fund_name="Cow says Moo",
        fund_symbol="MOO",
        policy_configuration=policy,
    )
    vault = Vault(vault_contract, comptroller_contract, deployment)

    policies = list(get_vault_policies(vault))
    assert len(policies) == 4


def test_redemption_time_lock(
    web3: Web3,
    deployment: EnzymeDeployment,
    user_1,
    usdc,
):
    """Do not allow arbitrage trades against share price by having a time lock on redemption.

    - Enzyme stores as ComptrollerLib.shareActionTimeLock variable
    """

    policy = create_safe_default_policy_configuration_for_generic_adapter(deployment)
    policy.shares_action_time_lock = 3600

    comptroller_contract, vault_contract = deployment.create_new_vault(
        user_1,
        usdc,
        fund_name="Cow says Moo",
        fund_symbol="MOO",
        policy_configuration=policy,
    )
    vault = Vault(vault_contract, comptroller_contract, deployment)

    # Set to 1h
    assert vault.comptroller.functions.getSharesActionTimelock().call() == 3600

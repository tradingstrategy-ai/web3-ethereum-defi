"""Deploy Enzyme protcol v4.

Based on https://github.com/enzymefinance/protocol/blob/v4/packages/protocol/tests/release/e2e/FundManagementWalkthrough.test.ts
"""

import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.vault import Vault
from eth_defi.event_reader.reader import extract_events
from eth_defi.trace import assert_transaction_success_with_explanation


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
    """Deploy Enzyme protocol, single USDC nominated vault and buy in."""

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

    comptroller, vault = deployment.create_new_vault(
        user_1,
        usdc,
    )

    assert comptroller.functions.getDenominationAsset().call() == usdc.address
    assert vault.functions.getTrackedAssets().call() == [usdc.address]

    # User 2 buys into the vault
    # See Shares.sol
    #
    # Buy shares for 500 USDC, receive min share
    tx_hash = usdc.functions.transfer(user_1, 500 * 10**6).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = usdc.functions.approve(comptroller.address, 500 * 10**6).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # See user 2 received shares
    balance = vault.functions.balanceOf(user_1).call()
    assert balance == 500 * 10**18


@pytest.mark.skip(reason="Unmaintained")
def test_vault_api(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    user_2: HexAddress,
    weth: Contract,
    mln: Contract,
    usdc: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
):
    """Test vault wrapper class."""

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

    comptroller_contract, vault_contract = deployment.create_new_vault(user_1, usdc, fund_name="Cow says Moo", fund_symbol="MOO")
    vault = Vault(vault_contract, comptroller_contract, deployment)

    assert vault.get_name() == "Cow says Moo"
    assert vault.get_symbol() == "MOO"
    assert vault.get_denomination_asset() == usdc.address
    assert vault.get_tracked_assets() == [usdc.address]

    # Accounting
    assert vault.get_total_supply() == 0
    assert vault.get_gross_asset_value() == 0
    assert vault.get_share_gross_asset_value() == 1 * 10**6

    # User 2 buys into the vault
    # See Shares.sol
    #
    # Buy shares for 500 USDC, receive min share
    tx_hash = usdc.functions.transfer(user_1, 500 * 10**6).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = usdc.functions.approve(vault.comptroller.address, 500 * 10**6).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = vault.comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": user_1})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert vault.get_total_supply() == 500 * 10**18
    assert vault.get_gross_asset_value() == 500 * 10**6
    assert vault.get_share_gross_asset_value() == 1 * 10**6

    # Denomination token checks
    assert vault.denomination_token.address == usdc.address
    assert vault.denomination_token.decimals == 6

    # Shares token checks
    assert vault.shares_token.address == vault_contract.address
    assert vault.shares_token.decimals == 18
    assert vault.shares_token.name == "Cow says Moo"
    assert vault.shares_token.symbol == "MOO"

    # Get the deployment event
    deployment_event = vault.fetch_deployment_event(reader=extract_events)
    assert deployment_event["blockNumber"] > 1

    # Test vault fetch
    comptroller2, vault2 = deployment.fetch_vault(vault.vault.address)
    assert vault2.address == vault.vault.address
    assert comptroller2.address == vault.comptroller.address


def test_fetch_deployment(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    user_2: HexAddress,
    weth: Contract,
    mln: Contract,
):
    """Fetch Enzyme deployment and vault information on-chain."""

    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        mln,
        weth,
    )

    fetched = EnzymeDeployment.fetch_deployment(web3, {"comptroller_lib": deployment.contracts.comptroller_lib.address})

    assert fetched.mln.address == mln.address
    assert fetched.weth.address == weth.address


def test_fetch_vault(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    user_2: HexAddress,
    usdc: Contract,
    weth: Contract,
    mln: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
):
    """Fetch existing Enzyme vault based on the vault address only."""

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

    comptroller_contract, vault_contract = deployment.create_new_vault(
        user_1,
        usdc,
    )

    vault = Vault.fetch(web3, vault_contract.address)

    assert vault.vault.address == vault_contract.address
    assert vault.comptroller.address == comptroller_contract.address

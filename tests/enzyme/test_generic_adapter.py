"""Test generic adapter on Enzyme.

"""
import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import encode_function_args, encode_function_call
from eth_defi.deploy import deploy_contract, get_or_create_contract_registry
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.generic_adapter import execute_calls_for_generic_adapter
from eth_defi.enzyme.vault import Vault
from eth_defi.trace import trace_evm_transaction, print_symbolic_trace, assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE


@pytest.fixture
def dual_token_deployment(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    user_2,
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

    deployment.add_primitive(
        usdc,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    deployment.add_primitive(
        weth,
        weth_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    return deployment


def test_generic_adapter_uniswap_v2(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    user_2,
    user_3,
    weth: Contract,
    mln: Contract,
    usdc: Contract,
    weth_usd_mock_chainlink_aggregator: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
    dual_token_deployment: EnzymeDeployment,
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: Contract,
):
    """Deploy Enzyme protocol with a generic adapter and trade with it

    - Deploy generic adapter

    - Make a trade for our Uniswap v2 using this adapter

    - See that fund shares are calculated correctly.

    See https://github.com/avantgardefinance/protocol/blob/feat/generic-adapter/tests/release/extensions/integration-manager/integrations/GenericAdapter.test.ts
    """

    # Check we have necessary Uniswap v2 liquidity available
    pair = uniswap_v2.get_pair_contract(weth.address, usdc.address)
    assert usdc.functions.balanceOf(pair.address).call() == 200_000 * 10**6
    assert weth.functions.balanceOf(pair.address).call() == 125 * 10**18

    deployment = dual_token_deployment

    comptroller, vault = deployment.create_new_vault(
        user_1,
        usdc,
    )

    generic_adapter = deploy_contract(
        web3,
        f"VaultSpecificGenericAdapter.json",
        deployer,
        deployment.contracts.integration_manager.address,
        vault.address,
    )

    assert generic_adapter.functions.getIntegrationManager().call() == deployment.contracts.integration_manager.address
    assert comptroller.functions.getDenominationAsset().call() == usdc.address
    assert vault.functions.getTrackedAssets().call() == [usdc.address]
    assert vault.functions.canManageAssets(user_1).call() is True

    # User 2 buys into the vault
    # See Shares.sol
    #
    # Buy shares for 500 USDC, receive min share
    usdc.functions.transfer(user_2, 500 * 10**6).transact({"from": deployer})
    usdc.functions.approve(comptroller.address, 500 * 10**6).transact({"from": user_2})
    comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": user_2})

    # Check that the vault has balance
    balance = usdc.functions.balanceOf(vault.address).call()
    assert balance == 500 * 10**6

    # Prepare the swap parameters
    usdc_swap_amount = 150 * 10**6  # 150 USDC
    spend_asset_amounts = [usdc_swap_amount]
    spend_assets = [usdc]
    path = [usdc.address, weth.address]
    expected_outgoing_amount, expected_incoming_amount = uniswap_v2.router.functions.getAmountsOut(usdc_swap_amount, path).call()
    assert expected_incoming_amount == pytest.approx(93398910964326424)  # Approx 0.09375 ETH + fees
    incoming_assets = [weth]
    min_incoming_assets_amounts = [expected_incoming_amount]

    # The vault performs a swap on Uniswap v2
    encoded_approve = encode_function_call(usdc.functions.approve, [uniswap_v2.router.address, usdc_swap_amount])

    encoded_swapExactTokensForTokens = encode_function_call(uniswap_v2.router.functions.swapExactTokensForTokens, [usdc_swap_amount, 1, path, generic_adapter.address, FOREVER_DEADLINE])

    bound_call = execute_calls_for_generic_adapter(
        comptroller=comptroller,
        external_calls=(
            (usdc, encoded_approve),
            (uniswap_v2.router, encoded_swapExactTokensForTokens),
        ),
        generic_adapter=generic_adapter,
        incoming_assets=incoming_assets,
        integration_manager=deployment.contracts.integration_manager,
        min_incoming_asset_amounts=min_incoming_assets_amounts,
        spend_asset_amounts=spend_asset_amounts,
        spend_assets=spend_assets,
    )

    tx_hash = bound_call.transact({"from": user_1, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Now after the swap the vault should have some WETH
    assert weth.functions.balanceOf(vault.address).call() == pytest.approx(93398910964326424)
    assert usdc.functions.balanceOf(vault.address).call() == 350 * 10**6


def test_generic_adapter_approve(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    user_2,
    user_3,
    weth: Contract,
    mln: Contract,
    usdc: Contract,
    weth_usd_mock_chainlink_aggregator: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
    dual_token_deployment: EnzymeDeployment,
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: Contract,
):
    """Check approve() sets allowance correctly for vault.

    - Any approvals will be set on GenericAdapter contract

    - Assets are transferred to GenericAdapter on
    """

    deployment = dual_token_deployment

    comptroller, vault = deployment.create_new_vault(
        user_1,
        usdc,
    )

    generic_adapter = deploy_contract(
        web3,
        f"VaultSpecificGenericAdapter.json",
        deployer,
        deployment.contracts.integration_manager.address,
        vault.address,
    )
    assert generic_adapter.functions.getIntegrationManager().call() == deployment.contracts.integration_manager.address

    usdc.functions.transfer(user_2, 500 * 10**6).transact({"from": deployer})
    usdc.functions.approve(comptroller.address, 500 * 10**6).transact({"from": user_2})
    comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": user_2})

    # Check that the vault has balance
    balance = usdc.functions.balanceOf(vault.address).call()
    assert balance == 500 * 10**6

    approve_amount = 100 * 10**6

    # The vault performs a swap on Uniswap v2
    encoded_approve = encode_function_call(usdc.functions.approve, [uniswap_v2.router.address, approve_amount])

    bound_call = execute_calls_for_generic_adapter(
        comptroller=comptroller,
        external_calls=((usdc, encoded_approve),),
        generic_adapter=generic_adapter,
        incoming_assets=[],
        integration_manager=deployment.contracts.integration_manager,
        min_incoming_asset_amounts=[],
        spend_asset_amounts=[],
        spend_assets=[],
    )

    tx_hash = bound_call.transact({"from": user_1, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert usdc.functions.allowance(generic_adapter.address, uniswap_v2.router.address).call() == approve_amount


def test_fetch_vault_with_generic_adapter(
    web3: Web3,
    deployer: HexAddress,
    user_1: HexAddress,
    user_2: HexAddress,
    usdc: Contract,
    weth: Contract,
    mln: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
):
    """Fetch existing Enzyme vault based with a named GenericAdapter contract."""

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

    generic_adapter = deploy_contract(
        web3,
        f"VaultSpecificGenericAdapter.json",
        deployer,
        deployment.contracts.integration_manager.address,
        vault_contract.address,
    )

    vault = Vault.fetch(web3, vault_contract.address, generic_adapter.address)
    assert vault.generic_adapter.address == generic_adapter.address

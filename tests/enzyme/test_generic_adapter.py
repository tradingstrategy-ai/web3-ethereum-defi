"""Test generic adapter on Enzyme.

"""
import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import encode_function_args
from eth_defi.deploy import deploy_contract, get_or_create_contract_registry
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.generic_adapter import execute_calls_for_generic_adapter
from eth_defi.trace import trace_evm_transaction, print_symbolic_trace
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE


@pytest.fixture
def dual_token_deployment(
        web3: Web3,
        deployer: HexAddress,
        fund_owner: HexAddress,
        fund_customer: HexAddress,
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
        fund_owner: HexAddress,
        fund_customer: HexAddress,
        fund_customer_2: HexAddress,
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
    assert usdc.functions.balanceOf(pair.address).call() == 200_000*10**6
    assert weth.functions.balanceOf(pair.address).call() == 125*10**18

    deployment = dual_token_deployment

    generic_adapter = deploy_contract(web3, f"enzyme/GenericAdapter.json", deployer, deployment.contracts.integration_manager.address)
    assert generic_adapter.functions.getIntegrationManager().call() == deployment.contracts.integration_manager.address

    comptroller, vault = deployment.create_new_vault(
        fund_owner,
        usdc,
    )

    assert comptroller.functions.getDenominationAsset().call() == usdc.address
    assert vault.functions.getTrackedAssets().call() == [usdc.address]
    assert vault.functions.canManageAssets(fund_owner).call() is True

    # User 2 buys into the vault
    # See Shares.sol
    #
    # Buy shares for 500 USDC, receive min share
    usdc.functions.transfer(fund_customer, 500 * 10 ** 6).transact({"from": deployer})
    usdc.functions.approve(comptroller.address, 500*10**6).transact({"from": fund_customer})
    comptroller.functions.buyShares(500*10**6, 1).transact({"from": fund_customer})

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
    # https://github.com/ethereum/web3.py/blob/168fceaf5c6829a8edeb510b997940064295ecf8/web3/_utils/contracts.py#L211
    encoded_approve = encode_function_args(
        weth.functions.approve,
        [uniswap_v2.router.address, usdc_swap_amount]
    )

    encoded_swapExactTokensForTokens = encode_function_args(
        uniswap_v2.router.functions.swapExactTokensForTokens,
        [usdc_swap_amount, 1, path, generic_adapter.address, FOREVER_DEADLINE]
    )

    bound_call = execute_calls_for_generic_adapter(
        comptroller=comptroller,
        external_calls=(
            (weth, encoded_approve),
            (uniswap_v2.router, encoded_swapExactTokensForTokens),
        ),
        generic_adapter=generic_adapter,
        incoming_assets=incoming_assets,
        integration_manager=deployment.contracts.integration_manager,
        min_incoming_asset_amounts=min_incoming_assets_amounts,
        spend_asset_amounts=spend_asset_amounts,
        spend_assets=spend_assets,
    )

    tx_hash = bound_call.transact({"from": fund_owner, "gas": 1_000_000})
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] == 0:
        # Explain why the transaction failed
        trace_data = trace_evm_transaction(web3, tx_hash)
        trace_output = print_symbolic_trace(get_or_create_contract_registry(web3), trace_data)
        print(trace_output)


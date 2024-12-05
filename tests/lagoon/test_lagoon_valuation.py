"""NAV calcualtion and valuation commitee tests."""

from decimal import Decimal

import pytest
from eth_typing import HexAddress
from multicall import Multicall
from safe_eth.eth.constants import NULL_ADDRESS
from web3 import Web3

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.safe.trace import assert_execute_module_success
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment, UniswapV2Deployment
from eth_defi.vault.base import TradingUniverse
from eth_defi.vault.valuation import NetAssetValueCalculator, UniswapV2Router02Quoter, Route
from tests.lagoon.conftest import topped_up_asset_manager


@pytest.fixture()
def uniswap_v2(web3):
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


def test_uniswap_v2_weth_usdc_sell_route(
    web3: Web3,
    lagoon_vault: LagoonVault,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    base_dino: TokenDetails,
    uniswap_v2: UniswapV2Deployment,
):
    """Test a simple WETH->USDC sell route on Uniswap v2.

    - See that the logic for a single route works

    - Test various ways of building the tx payload for eth_call

    - Router address is 0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24

    - Dino amount is 547942000069182639312002

    - Dino PATH is ["0x85E90a5430AF45776548ADB82eE4cD9E33B08077", "0x4200000000000000000000000000000000000006", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"]

    - Dino value  0.0000673 * 547942 = $36.876496599999996
    """

    uniswap_v2_quoter_v2 = UniswapV2Router02Quoter(
        uniswap_v2.router,
        debug=True,
    )

    route = Route(
        source_token=base_weth,
        target_token=base_usdc,
        quoter=uniswap_v2_quoter_v2,
        path=(base_weth.address, base_usdc.address),
    )

    # Sell 1000 WETH
    amount = 1000 * 10**18
    wrapped_call = uniswap_v2_quoter_v2.create_multicall_wrapper(route, amount)

    assert wrapped_call.contract_address == "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24"

    test_call_result = uniswap_v2_quoter_v2.swap_router_v2.functions.getAmountsOut(amount, route.path).call()
    assert test_call_result is not None

    # Another method to double check call data encoding
    tx_data_2 = uniswap_v2_quoter_v2.swap_router_v2.functions.getAmountsOut(amount, route.path).build_transaction(
        {"from": NULL_ADDRESS}
    )
    correct_bytes = tx_data_2["data"][2:]

    tx_data = wrapped_call.create_tx_data()
    assert tx_data["data"].hex() == correct_bytes

    # 0xd06ca61f00000000000000000000000000000002f050fe938943acc45f65568000000000000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000000020000000000000000000000004200000000000000000000000000000000000006000000000000000000000000833589fcd6edb6e08f4c7c32d4f71b54bda02913
    try:
        raw_result = web3.eth.call(tx_data)
    except Exception as e:
        # If this fails, just punch in the data to Tenderly Simulate transaction do debug
        raise AssertionError(wrapped_call.get_debug_string()) from e

    assert raw_result is not None

    # Now using Multicall
    multicall = Multicall(
        calls=[wrapped_call.create_multicall()],
        block_id=web3.eth.block_number,
        _w3=web3,
        require_success=False,
        gas_limit=10_000_000,
    )
    batched_result = multicall()
    result = batched_result[route]
    assert result is not None, f"Reading quoter using Multicall failed"


def test_lagoon_calculate_portfolio_nav(
    web3: Web3,
    lagoon_vault: LagoonVault,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    base_dino: TokenDetails,
    uniswap_v2: UniswapV2Deployment,
):
    """Calculate NAV for a simple Lagoon portfolio

    - Portfolio contains only WETH, USDC

    - No intermediate tokens
    """
    vault = lagoon_vault

    universe = TradingUniverse(
        spot_token_addresses={
            base_weth.address,
            base_usdc.address,
            base_dino.address,
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.get_position_count() == 3

    uniswap_v2_quoter_v2 = UniswapV2Router02Quoter(uniswap_v2.router)

    nav_calculator = NetAssetValueCalculator(
        web3,
        denomination_token=base_usdc,
        intermediary_tokens={base_weth.address},  # Allow DINO->WETH->USDC
        quoters={uniswap_v2_quoter_v2},
        debug=True,
    )

    #                                  Asset                                     Address        Balance                   Router Works  Value
    #             Path
    #             USDC                  USDC  0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913           0.35                            yes   0.35
    #             WETH -> USDC          WETH  0x4200000000000000000000000000000000000006       0.000000  UniswapV2Router02Quoter   yes   0.00
    #             DINO -> USDC          DINO  0x85E90a5430AF45776548ADB82eE4cD9E33B08077  547942.000069  UniswapV2Router02Quoter    no      -
    #             DINO -> WETH -> USDC  DINO  0x85E90a5430AF45776548ADB82eE4cD9E33B08077  547942.000069  UniswapV2Router02Quoter   yes  36.69

    portfolio_valuation = nav_calculator.calculate_market_sell_nav(portfolio)
    assert portfolio_valuation.denomination_token == base_usdc
    assert len(portfolio_valuation.spot_valuations) == 3
    assert portfolio_valuation.spot_valuations[base_usdc.address] == pytest.approx(Decimal(0.347953))
    assert portfolio_valuation.spot_valuations[base_weth.address] == pytest.approx(Decimal(0))
    assert portfolio_valuation.spot_valuations[base_dino.address] > 0
    assert portfolio_valuation.get_total_equity() > 0


def test_lagoon_diagnose_routes(
    web3: Web3,
    lagoon_vault: LagoonVault,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    base_dino: TokenDetails,
    uniswap_v2: UniswapV2Deployment,
):
    """Run route diagnostics.
    """
    vault = lagoon_vault

    universe = TradingUniverse(
        spot_token_addresses={
            base_weth.address,
            base_usdc.address,
            base_dino.address,
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.get_position_count() == 3

    uniswap_v2_quoter_v2 = UniswapV2Router02Quoter(uniswap_v2.router)

    nav_calculator = NetAssetValueCalculator(
        web3,
        denomination_token=base_usdc,
        intermediary_tokens={base_weth.address},  # Allow DINO->WETH->USDC
        quoters={uniswap_v2_quoter_v2},
        debug=True,
    )

    routes = nav_calculator.create_route_diagnostics(portfolio)

    print()
    print(routes)

    assert routes.loc["USDC"]["Value"] is not None
    assert routes.loc["WETH -> USDC"]["Value"] is not None
    assert routes.loc["DINO -> WETH -> USDC"]["Value"] is not None
    assert routes.loc["DINO -> USDC"]["Value"] == "-"


def test_lagoon_post_valuation(
    web3: Web3,
    lagoon_vault: LagoonVault,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    base_dino: TokenDetails,
    uniswap_v2: UniswapV2Deployment,
    topped_up_valuation_manager: HexAddress,
    topped_up_asset_manager: HexAddress,
):
    """Update vault NAV.

    - Value vault portfolio

    - Post NAV update using Roles multisig hack

    - Read back the share price

    .. code-block:: shell

        JSON_RPC_TENDERLY="https://virtual.base.rpc.tenderly.co/ae8c0d9c-b013-47fb-bdf5-eac4f888a5db" pytest -k test_lagoon_post_valuation
    """

    vault = lagoon_vault
    valuation_manager = topped_up_valuation_manager
    asset_manager = topped_up_asset_manager

    # Check value before update
    # settle() never called for this vault, so the value is zero
    nav = vault.fetch_nav()
    assert nav == pytest.approx(Decimal(0))

    universe = TradingUniverse(
        spot_token_addresses={
            base_weth.address,
            base_usdc.address,
            base_dino.address,
        }
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.get_position_count() == 3

    uniswap_v2_quoter_v2 = UniswapV2Router02Quoter(uniswap_v2.router)

    nav_calculator = NetAssetValueCalculator(
        web3,
        denomination_token=base_usdc,
        intermediary_tokens={base_weth.address},  # Allow DINO->WETH->USDC
        quoters={uniswap_v2_quoter_v2},
        debug=True,
    )

    portfolio_valuation = nav_calculator.calculate_market_sell_nav(portfolio)

    # First post the new valuation as valuation manager
    total_value = portfolio_valuation.get_total_equity()
    bound_func = vault.post_new_valuation(total_value)
    tx_hash = bound_func.transact({"from": valuation_manager})      # Unlocked by anvil
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check we have no pending redemptions (might abort settle)
    redemption_shares = vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number)
    assert redemption_shares == 0

    # Then settle the valuation as the vault owner (Safe multisig) in this case
    settle_call = vault.settle()
    moduled_tx = vault.transact_through_module(settle_call)
    tx_data = moduled_tx.build_transaction({
        "from": asset_manager,
    })
    # Normal estimate_gas does not give enough gas for
    # Safe execTransactionFromModule() transaction for some reason
    gnosis_gas_fix = 1_000_000
    tx_data["gas"] = web3.eth.estimate_gas(tx_data) + gnosis_gas_fix
    tx_hash = web3.eth.send_transaction(tx_data)
    assert_execute_module_success(web3, tx_hash)

    # Check value after update.
    # We should have USDC value of the vault readable
    # from NAV smart contract endpoint
    nav = vault.fetch_nav()
    assert nav > Decimal(30)  # Changes every day as we need to test live mainnet

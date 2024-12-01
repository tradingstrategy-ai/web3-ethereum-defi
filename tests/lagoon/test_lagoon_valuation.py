"""NAV calcualtion and valuation commitee tests."""

from decimal import Decimal

import pytest
from safe_eth.eth.constants import NULL_ADDRESS
from web3 import Web3

from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment, UniswapV2Deployment
from eth_defi.vault.base import TradingUniverse
from eth_defi.vault.valuation import NetAssetValueCalculator, UniswapV2Router02Quoter, Route


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
    wrapped_call = uniswap_v2_quoter_v2.create_multicall(route, 1000 * 10**18)
    tx_data = wrapped_call.create_tx_data()

    try:
        raw_result = web3.eth.call(tx_data)
    except Exception as e:
        # If this fails, just punch in the data to Tenderly Simulate transaction do debug
        raise AssertionError(f"Could not execute the getAmountOuts().\nAddress: {tx_data['to']}\nArgs: {wrapped_call.get_args()}, Data: {tx_data['data'].hex()}") from e

    assert raw_result > 100


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

    portfolio_valuation = nav_calculator.calculate_market_sell_nav(portfolio)
    assert len(portfolio_valuation.spot_valutions) == 2
    assert portfolio_valuation.get_total_equity() == Decimal(1.2)


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



"""NAV calcualtion and valuation commitee tests."""

from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.event_reader.multicall_batcher import get_multicall_contract, call_multicall_batched_single_thread, MulticallWrapper
from eth_defi.lagoon.vault import LagoonVault
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.safe.trace import assert_execute_module_success
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import fetch_deployment, UniswapV2Deployment
from eth_defi.abi import ZERO_ADDRESS
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import fetch_deployment as fetch_deployment_uni_v3, UniswapV3Deployment
from eth_defi.uniswap_v3.utils import encode_path

from eth_defi.vault.base import TradingUniverse, VaultPortfolio
from eth_defi.vault.mass_buyer import create_buy_portfolio, BASE_SHOPPING_LIST, buy_tokens
from eth_defi.vault.valuation import NetAssetValueCalculator, UniswapV2Router02Quoter, Route, UniswapV3Quoter


@pytest.fixture()
def uniswap_v2(web3):
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


@pytest.fixture()
def uniswap_v3(web3):
    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3_on_base = fetch_deployment_uni_v3(
        web3,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data["quoter_v2"],
        router_v2=deployment_data["router_v2"],
    )
    return uniswap_v3_on_base


@pytest.fixture()
def multicall_batch_size() -> int:
    """Keep it low, Anvil very slow"""
    return 3


@pytest.fixture()
def extensive_portfolio(
    web3,
    lagoon_vault: LagoonVault,
    base_usdc,
    base_weth,
    uniswap_v2,
    uniswap_v3,
    usdc_holder,
    topped_up_asset_manager,
    multicall_batch_size,
) -> VaultPortfolio:
    """Make a shopping list of Base tokens.

    - Acquire some more tokens for the tests, each 5 USDC.
      Mixed Uniswap v2/v3 routing.

    - Fixture slow as we brute force paths
    """

    # Top up the vault with 999 USDC
    tx_hash = base_usdc.contract.functions.transfer(lagoon_vault.safe_address, 999 * 10**6).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    portfolio = create_buy_portfolio(
        BASE_SHOPPING_LIST,
        Decimal(5.0)
    )

    buy_result = buy_tokens(
        web3,
        user=lagoon_vault.safe_address,  # We cheat by having this address unlockeed in Anvl
        portfolio=portfolio,
        denomination_token=base_usdc,
        intermediary_tokens={base_weth},
        quoters={
            UniswapV2Router02Quoter(swap_router_v2=uniswap_v2.router),
            UniswapV3Quoter(quoter=uniswap_v3.quoter),
        },
        uniswap_v2=uniswap_v2,
        uniswap_v3=uniswap_v3,
        multicall_batch_size=multicall_batch_size,
    )

    assert len(buy_result.needed_transactions) > 0

    # Asset manager executes approve + swap texs for all tokens we want to buy
    for call in buy_result.needed_transactions:
        assert isinstance(call, ContractFunction)
        try:
            wrapped_call = lagoon_vault.transact_via_exec_module(call)
        except Exception as e:
            # Annoying checksum address
            raise RuntimeError(f"Wrapped call failed: {call}") from e
        tx_data = wrapped_call.build_transaction({"from": topped_up_asset_manager})
        tx_data["gas"] = tx_data["gas"] + 1_000_000   # Gnosis tx tend to underestimate gas
        tx_hash = web3.eth.send_transaction(tx_data)
        assert_execute_module_success(web3, tx_hash)

    return portfolio


@pytest.fixture()
def vault_with_more_tokens(web3, lagoon_vault, extensive_portfolio):
    """Execute portfolio buys for the vault."""
    vault = lagoon_vault
    return vault


def test_uniswap_v3_quoter_basic_three_leg(
    web3: Web3,
    uniswap_v3: UniswapV3Deployment,
    base_usdc,
    base_weth,
):
    """Check the underlying quoter smart contract works."""

    quoter = uniswap_v3.quoter
    parts = [
        base_usdc.address,
        base_weth.address,
        "0x9a26f5433671751c3276a065f57e5a02d2817973",  # ODOS
    ]
    fees = [
        5 * 100,
        30 * 100,
    ]
    path = encode_path(
        parts,
        fees
    )
    amount = 5 * 10**6

    # Try Web3.py native encoding
    quote_call  = quoter.functions.quoteExactInput(
        path,
        amount
    )
    quote_result = quote_call.call()
    amount_out_1 = quote_result[0]
    assert amount_out_1 > 10**18

    # Try passing data blob around
    data = quote_call.build_transaction()["data"]
    assert len(bytes.fromhex(data[2:])) == 196
    quote_result_bytes = web3.eth.call({
        "to": quoter.address,
        "data": data,
    })
    amount_out_2 = int.from_bytes(quote_result_bytes[0:32])
    assert amount_out_2 == amount_out_1


def test_uniswap_v3_quoter_basic_token_missing(
    web3: Web3,
    uniswap_v3: UniswapV3Deployment,
    base_usdc,
    base_weth,
):
    """Uni v3 does not have Keycat pair."""

    quoter = uniswap_v3.quoter
    parts = [
        base_usdc.address,
        base_weth.address,
        "0x9a26f5433671751c3276a065f57e5a02d2817973",  # Keycat
    ]
    fees = [
        5 * 100,
        30 * 100,
    ]
    path = encode_path(
        parts,
        fees
    )
    amount = 5 * 10**6

    # Try Web3.py native encoding
    quote_call  = quoter.functions.quoteExactInput(
        path,
        amount
    )
    quote_result = quote_call.call()
    amount_out_1 = quote_result[0]
    assert amount_out_1 > 10**18

    # Try passing data blob around
    data = quote_call.build_transaction()["data"]
    assert len(bytes.fromhex(data[2:])) == 196
    quote_result_bytes = web3.eth.call({
        "to": quoter.address,
        "data": data,
    })
    amount_out_2 = int.from_bytes(quote_result_bytes[0:32])
    assert amount_out_2 == amount_out_1


@pytest.mark.skip(reason="Broken, please fix")
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
        path=[base_weth, base_usdc],
        quoter=uniswap_v2_quoter_v2,
    )

    # Sell 1000 WETH
    amount = 1000 * 10**18
    wrapped_call = uniswap_v2_quoter_v2.create_multicall_wrapper(route, amount)

    assert wrapped_call.contract_address == "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24"

    test_call_result = uniswap_v2_quoter_v2.swap_router_v2.functions.getAmountsOut(amount, route.address_path).call()
    assert test_call_result is not None

    # Another method to double check call data encoding
    bound_call = uniswap_v2_quoter_v2.swap_router_v2.functions.getAmountsOut(amount, route.address_path)
    tx_data_2 = bound_call.build_transaction(
        {"from": ZERO_ADDRESS}
    )
    correct_bytes = tx_data_2["data"][2:]

    address, data = wrapped_call.get_address_and_data()
    tx_data ={
        "data": data,
        "address": address,
    }
    assert tx_data["data"].hex()[2:] == correct_bytes

    # 0xd06ca61f00000000000000000000000000000002f050fe938943acc45f65568000000000000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000000020000000000000000000000004200000000000000000000000000000000000006000000000000000000000000833589fcd6edb6e08f4c7c32d4f71b54bda02913
    try:
        raw_result = web3.eth.call(tx_data)
    except Exception as e:
        # If this fails, just punch in the data to Tenderly Simulate transaction do debug
        raise AssertionError(f"God: {wrapped_call}") from e

    assert raw_result is not None

    multicall_contract =  get_multicall_contract(web3)
    batched_result = call_multicall_batched_single_thread(
        multicall_contract,
        calls=[MulticallWrapper(call=bound_call, debug=False)]
    )
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

    # Very small value, will sell for 0
    assert portfolio.spot_erc20[base_weth.address] == Decimal(10) ** -16

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

    assert routes.loc[routes["Path"] == "USDC"]["Value"] is not None
    assert routes.loc[routes["Path"] == "WETH -> USDC"]["Value"] is not None
    assert routes.loc[routes["Path"] == "DINO -> WETH -> USDC"]["Value"] is not None
    assert routes.loc[routes["Path"] == "DINO -> USDC"]["Value"].iloc[0] == "-"


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
    assert total_value > 10  # 0.30 USDC

    bound_func = vault.post_new_valuation(total_value)
    tx_hash = bound_func.transact({"from": valuation_manager})      # Unlocked by anvil
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check we have no pending redemptions (might abort settle)
    redemption_shares = vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number)
    assert redemption_shares == 0

    # Then settle the valuation as the vault owner (Safe multisig) in this case
    settle_call = vault.vault_contract.functions.settleDeposit()
    moduled_tx = vault.transact_via_exec_module(settle_call)
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


def test_valuation_mixed_routes(
    web3: Web3,
    vault_with_more_tokens: LagoonVault,
    extensive_portfolio: VaultPortfolio,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    base_dino: TokenDetails,
    uniswap_v2: UniswapV2Deployment,
    uniswap_v3: UniswapV3Deployment,
    topped_up_valuation_manager: HexAddress,
    topped_up_asset_manager: HexAddress,
):
    """Value a portfolio with mixed Uniswap v2/v3 routes.

    - Buy some random tokens, on the top of the existing tokens the address already helds

    - See that the valuation of bought tokens match what was the buy price

    - Do miked two leg/three leg/uniswap v2/uniswap v3 routing

    - Use lagoon, but the valuation itself does not care about Lagoon

    - This test is very slow due to high number of Multicalls made
    """

    chain_id = web3.eth.chain_id
    vault = vault_with_more_tokens

    all_tokens = {
        # base_weth.address,  Wrapped ETH valuation will fail, because the value is too low
        base_usdc.address,
        base_dino.address,
    } | extensive_portfolio.tokens

    all_tokens = sorted(all_tokens)  # Deterministic

    for addr in all_tokens:
        token = fetch_erc20_details(web3, addr, chain_id=chain_id)
        balance = token.fetch_balance_of(vault.safe_address)
        assert balance > 0, f"No token {token} in vault {vault}"

    universe = TradingUniverse(
        spot_token_addresses=all_tokens,
    )
    latest_block = get_almost_latest_block_number(web3)
    portfolio = vault.fetch_portfolio(universe, latest_block)
    assert portfolio.get_position_count() == 7

    uniswap_v2_quoter = UniswapV2Router02Quoter(uniswap_v2.router)
    uniswap_v3_quoter = UniswapV3Quoter(uniswap_v3.quoter)

    nav_calculator = NetAssetValueCalculator(
        web3,
        denomination_token=base_usdc,
        intermediary_tokens={base_weth.address},
        quoters={uniswap_v2_quoter, uniswap_v3_quoter},
        debug=True,
    )

    # We bought using 5 USD, so all token holding valuations should be in ballpark
    portfolio_valuation = nav_calculator.calculate_market_sell_nav(portfolio)
    assert portfolio_valuation.spot_valuations["0x9a26f5433671751c3276a065f57e5a02d2817973"] > 4.5  # Keycat
    assert portfolio_valuation.spot_valuations["0x7484a9fb40b16c4dfe9195da399e808aa45e9bb9"] > 4.5  # AGNT

    # Check routes
    routes = nav_calculator.create_route_diagnostics(portfolio)
    print()
    print(routes)
    assert len(routes) > 0

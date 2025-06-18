"""Create token buy lists for testing."""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias, Iterable

from eth_typing import HexAddress, BlockIdentifier
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.token import TokenDetails, get_erc20_contract
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection as swap_with_slippage_protection_uni_v2
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection as swap_with_slippage_protection_uni_v3
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.vault.base import VaultPortfolio
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.vault.valuation import NetAssetValueCalculator, Route, ValuationQuoter

logger = logging.getLogger(__name__)

TokenTradeDefinition: TypeAlias = tuple[str, str, str]


BASE_SHOPPING_LIST: list[TokenTradeDefinition] = [
    ("uniswap-v2", "keycat", "0x9a26f5433671751c3276a065f57e5a02d2817973"),  # KEYCAT-WETH
    ("uniswap-v3", "odos", "0xca73ed1815e5915489570014e024b7ebe65de679"),  # ODOS-WETH
    ("uniswap-v3", "cbBTC", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"),  # CBBTC-USDC
    ("uniswap-v2", "AGNT", "0x7484a9fb40b16c4dfe9195da399e808aa45e9bb9"),  # AGNT-USDC
    ("uniswap-v3", "SIMMI", "0x161e113b8e9bbaefb846f73f31624f6f9607bd44"),  # Uniswap v3 only
]


@dataclass(frozen=True, slots=True)
class BuyResult:
    needed_transactions: list[ContractFunction]
    taken_routes: dict[TokenDetails, Route]


def _default_buy_function(
    web3,
    user: HexAddress,
    route: Route,
    amount: Decimal,
    uniswap_v2: UniswapV2Deployment,
    uniswap_v3: UniswapV3Deployment,
) -> Iterable[ContractFunction]:
    """Buy tokens.

    :param user:
        Buyer address.

        Assume unlocked Anvil ccount.
    """

    assert isinstance(route, Route)
    assert isinstance(amount, Decimal)

    source_token = route.source_token
    raw_amount = source_token.convert_to_raw(amount)

    logger.info("About to buy %s", route.quoter.format_path(route))

    match route.dex_hint:
        case "uniswap-v2":
            assert uniswap_v2, "Uniswap v2 deployment must be given"
            assert len(route.path) in (2, 3), f"Long paths not supported: {route.path}"
            intermediate_token = route.intermediate_token
            existing_balance = source_token.fetch_balance_of(user)
            assert existing_balance > amount, f"Not enough token {source_token.symbol} to approve(). Has {existing_balance}, need {amount}"
            yield source_token.contract.functions.approve(uniswap_v2.router.address, raw_amount)
            yield swap_with_slippage_protection_uni_v2(
                uniswap_v2_deployment=uniswap_v2,
                recipient_address=user,
                quote_token=route.source_token.contract,
                base_token=route.target_token.contract,
                intermediate_token=intermediate_token,
                amount_in=raw_amount,
            )
        case "uniswap-v3":
            assert uniswap_v3, "Uniswap v3 deployment must be given"
            assert len(route.path) in (2, 3), f"Long paths not supported: {route.path}"
            intermediate_token = route.intermediate_token
            existing_balance = source_token.fetch_balance_of(user)
            assert existing_balance > amount, f"Not enough token {source_token.symbol} to approve(). Has {existing_balance}, need {amount}"
            yield source_token.contract.functions.approve(uniswap_v3.swap_router.address, raw_amount)
            yield swap_with_slippage_protection_uni_v3(
                uniswap_v3_deployment=uniswap_v3,
                recipient_address=user,
                quote_token=route.source_token.contract,
                base_token=route.target_token.contract,
                intermediate_token=intermediate_token,
                amount_in=raw_amount,
                pool_fees=route.fees,
            )
        case _:
            raise NotImplementedError(f"Unknown dex_hint {route.dex_hint} for {route}")


def create_buy_portfolio(
    tokens: list[TokenTradeDefinition],
    amount_denomination_token: Decimal,
) -> VaultPortfolio:
    """Create a portfolio of tokens to buy based on given Python."""
    buy_portfolio = VaultPortfolio(
        spot_erc20=LowercaseDict(**{t[2]: amount_denomination_token for t in tokens}),
        dex_hints={t[2]: t[0] for t in tokens},
    )
    return buy_portfolio


def buy_tokens(
    web3: Web3,
    user: HexAddress,
    portfolio: VaultPortfolio,
    denomination_token: HexAddress | TokenDetails,
    intermediary_tokens: set[HexAddress | TokenDetails],
    quoters: set[ValuationQuoter],
    multicall: bool | None = None,
    block_identifier: BlockIdentifier = None,
    multicall_gas_limit=10_000_000,
    buy_func=_default_buy_function,
    uniswap_v2: UniswapV2Deployment | None = None,
    uniswap_v3: UniswapV3Deployment | None = None,
    multicall_batch_size: int = 5,
) -> BuyResult:
    """Buy bunch of tokens on the wish list.

    - User for testing
    - Automatically resolve the routes with the best quote
    """

    user = Web3.to_checksum_address(user)
    logger.info("Preparing mass buy %d tokens, sending to %s", len(portfolio.tokens), user)

    nav = NetAssetValueCalculator(
        web3=web3,
        denomination_token=denomination_token,
        intermediary_tokens=intermediary_tokens,
        quoters=quoters,
        multicall=multicall,
        batch_size=multicall_batch_size,
    )

    swap_matrix = nav.find_swap_routes(portfolio)

    used_routes: dict[TokenDetails, Route] = {}

    calls = []

    for token, route_tuple in swap_matrix.best_results_by_token.items():
        assert len(route_tuple) > 0

        best_option = route_tuple[0]
        best_route, expected_receive = best_option

        logger.info(
            "Buying %s using route %s, got %d options, expected amount %s, for %s",
            token,
            best_route,
            len(route_tuple),
            expected_receive,
            user,
        )

        assert expected_receive is not None, f"Could not find working routes for token {token.symbol}.Routes are:\n{route_tuple}"

        buy_amount = portfolio.spot_erc20[token.address]

        # Generate both approve and swap txs
        for call in buy_func(
            web3=web3,
            user=user,
            route=best_route,
            amount=buy_amount,
            uniswap_v2=uniswap_v2,
            uniswap_v3=uniswap_v3,
        ):
            assert isinstance(call, ContractFunction)
            calls.append(call)

        used_routes[token] = best_route

    return BuyResult(
        needed_transactions=calls,
        taken_routes=used_routes,
    )

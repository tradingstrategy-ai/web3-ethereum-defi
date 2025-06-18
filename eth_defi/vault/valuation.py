"""Net asset valuation calculations for token portfolios and vaults.

- Calculate the value of vault portfolio using only onchain data,
  available from JSON-RPC

- Find best routes to buy tokens, which result to the best price, using brute force

- See :py:class:`NetAssetValueCalculator` for usage

"""

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Any, TypeAlias, Hashable

import pandas as pd
from eth_typing import HexAddress, BlockIdentifier
from matplotlib._api import classproperty
from multicall import Call, Multicall
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import decode_function_output
from eth_defi.event_reader.multicall_batcher import get_multicall_contract, call_multicall_batched_single_thread, MulticallWrapper, call_multicall_debug_single_thread
from eth_defi.provider.anvil import is_mainnet_fork
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails, fetch_erc20_details, TokenAddress
from eth_defi.uniswap_v3.utils import encode_path
from eth_defi.vault.base import VaultPortfolio
from eth_defi.vault.lower_case_dict import LowercaseDict

logger = logging.getLogger(__name__)


TokenAmount: TypeAlias = Decimal


class NoRouteFound(Exception):
    """We could not route some of the spot tokens to get any valuations for them."""


@dataclass(slots=True)
class PortfolioValuation:
    """Valuation calulated for a portfolio.

    See :py:class:`eth_defi.vault.base.VaultPortfolio` for the portfolio itself.
    """

    #: The reserve currency of this vault
    denomination_token: TokenDetails

    #: Individual spot valuations
    spot_valuations: dict[HexAddress, Decimal]

    def __post_init__(self):
        for key, value in self.spot_valuations.items():
            assert isinstance(value, Decimal), f"Valuation result was not Decimal number {key}: {value}"

    def get_total_equity(self) -> Decimal:
        """How much we value this portfolio in the :py:attr:`denomination_token`"""
        return sum(self.spot_valuations.values())


@dataclass(frozen=True, slots=True)
class SwapMatrix:
    """Brute-forced route swap result for a portfolio of buying multiple tokens.

    See :py:meth:`NetAssetValueCalculator.find_swap_routes`
    """

    #: Outcome of different attempted routes.
    #:
    #: Result is none if the path did not exist or the smart contract call failed.
    #:
    results: dict["Route", Decimal | None]
    best_results_by_token: dict[TokenDetails, list[tuple["Route", Decimal | None]]]

    @property
    def tokens(self) -> set:
        return set(self.best_results_by_token.keys())


@dataclass(slots=True, frozen=True)
class Route:
    """One potential swap path.

    - Support paths with 2 or 3 pairs

    - Present one potential swap path between source and target

    - Routes can contain any number of intermediate tokens in the path

    - Used to ABI encode for multicall calls
    """

    #: What router we use
    quoter: "ValuationQuoter"

    #: What route path we take
    path: tuple[TokenDetails, TokenDetails] | tuple[TokenDetails, TokenDetails, TokenDetails]

    #: Fees between pools for Uni v3
    fees: tuple[int] | tuple[int, int] | None = None

    def __post_init__(self):
        assert isinstance(self.path[0], TokenDetails), f"Got {self.path[0]}"
        assert isinstance(self.path[1], TokenDetails), f"Got {self.path[1]}"
        if self.fees:
            for f in self.fees:
                assert type(f), f"Got {f}"

    def __repr__(self):
        return f"<Route {self.get_formatted_path()} using quoter {self.quoter.dex_hint}>"

    def __hash__(self) -> int:
        """Unique hash for this instance"""
        return hash((self.dex_hint, self.path, self.fees))

    def __eq__(self, other: "Route") -> bool:
        # fmt: off
        return self.path == other.path and \
               self.dex_hint == other.dex_hint  and \
               self.fees == other.fees
        # fmt: on

    @property
    def source_token(self) -> TokenDetails:
        return self.path[0]

    @property
    def target_token(self) -> TokenDetails:
        return self.path[-1]

    @property
    def intermediate_token(self) -> TokenDetails | None:
        if len(self.path) == 3:
            return self.path[1]
        return None

    @property
    def function_signature_string(self) -> str:
        return self.signature[0]

    @property
    def token(self) -> TokenDetails:
        return self.source_token

    @property
    def dex_hint(self) -> str:
        return self.quoter.dex_hint

    @property
    def address_path(self) -> list[str]:
        return [Web3.to_checksum_address(x.address) for x in self.path]

    def get_formatted_path(self) -> str:
        """Return human readable path."""
        return self.quoter.format_path(self)


@dataclass(slots=True, frozen=True)
class ValuationMulticallWrapper(MulticallWrapper):
    """Wrap the undertlying Multicall with diagnostics data.

    - Because the underlying Multicall lib is not powerful enough.

    - And we do not have time to fix it
    """

    quoter: "ValuationQuoter"
    route: Route
    amount_in: int

    def __repr__(self):
        return f"<ValuationMulticallWrapper on DEX:{self.quoter.dex_hint}, route:{self.quoter.format_path(self.route)}, amount in:{self.amount_in} using func:{self.call.fn_name}>"

    def get_key(self) -> Hashable:
        return self.route

    def get_human_id(self) -> str:
        return str(self.get_key())

    def create_multicall(self) -> Call:
        """Create underlying call about."""
        call = Call(self.contract_address, self.signature, [(self.route, self)])
        return call

    def handle(self, success, raw_return_value: bytes) -> TokenAmount | None:
        if not success:
            return None

        try:
            token_amount = self.quoter.handle_onchain_return_value(
                self,
                raw_return_value,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to decode. Quoter {self.quoter}, return dadta {raw_return_value}") from e
        return token_amount


class ValuationQuoter(ABC):
    """Handle asset valuation on a specific DEX/quoter.

    - Takes in source and target tokens as input and generate all routing path combinations

    - Creates routes to a specific DEX

    - Each DEX has its own quoter contract we need to integrate

    - Resolves the onchain Solidity function return value to a token amount we get
    """

    def __init__(self, debug: bool = False):
        self.debug = debug

    @abstractmethod
    def generate_routes(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
        amount: Decimal,
        debug: bool,
    ) -> Iterable[Route]:
        # Direct route
        yield ()

    @abstractmethod
    def handle_onchain_return_value(
        self,
        route: Route,
        raw_return_value: any,
    ):
        pass

    @abstractmethod
    def create_multicall_wrapper(self, route: Route, amount_in: int) -> ValuationMulticallWrapper:
        pass

    @abstractmethod
    def format_path(self, route: Route) -> str:
        """Get human-readable route path line."""

    @classmethod
    @abstractmethod
    def dex_hint(cls) -> str:
        """Return string id used to identify this DEX.

        E.g. ``uniswap-v2``.
        """


class UniswapV2Router02Quoter(ValuationQuoter):
    """Handle Uniswap v2 quoters using Router02 contract.

    - https://docs.uniswap.org/contracts/v2/reference/smart-contracts/router-02#getamountsout

    - https://basescan.org/address/0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24#readContract
    """

    #: Quoter signature string for Multicall lib.
    #:
    #: Not the standard string signature format,
    #: because Multicall lib wants it special output format suffix here
    signature_string = "getAmountsOut(uint256,address[])(uint256[])"

    def __init__(
        self,
        swap_router_v2: Contract,
        debug: bool = False,
    ):
        super().__init__(debug=debug)
        assert isinstance(swap_router_v2, Contract)
        self.swap_router_v2 = swap_router_v2

    def __repr__(self):
        return f"<UniswapV2Router02Quoter({self.swap_router_v2.address})>"

    @classproperty
    def dex_hint(cls) -> str:
        return "uniswap-v2"

    def create_multicall_wrapper(self, route: Route, amount_in: int) -> ValuationMulticallWrapper:
        # If we need to optimise Python parsing speed, we can directly pass function selectors and pre-packed ABI
        bound_func = self.swap_router_v2.functions.getAmountsOut(amount_in, route.address_path)
        return ValuationMulticallWrapper(
            quoter=self,
            route=route,
            amount_in=amount_in,
            debug=self.debug,
            call=bound_func,
        )

    def generate_routes(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
        amount: Decimal,
        debug: bool,
    ) -> Iterable[Route]:
        """Create routes we need to test on Uniswap v2"""

        for path in self.get_path_combinations(
            source_token,
            target_token,
            intermediate_tokens,
        ):
            yield Route(
                quoter=self,
                path=path,
            )

    def handle_onchain_return_value(
        self,
        wrapper: ValuationMulticallWrapper,
        raw_return_value: bytes,
    ) -> Decimal | None:
        """Convert getAmountsOut() return value to tokens we receive"""
        route = wrapper.route
        func = self.swap_router_v2.functions.getAmountsOut(wrapper.amount_in, wrapper.route.address_path)
        decoded = decode_function_output(func, raw_return_value)
        target_token_out = decoded[0][-1]
        human_out = route.target_token.convert_to_decimals(target_token_out)
        logger.info(
            "Uniswap V2, path %s resolved, %s %s -> %s %s",
            route.get_formatted_path(),
            route.source_token.convert_to_decimals(wrapper.amount_in),
            route.source_token.symbol,
            human_out,
            route.target_token.symbol,
        )
        return human_out

    def get_path_combinations(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
    ) -> Iterable[list[TokenDetails]]:
        """Generate Uniswap v2 swap paths with all supported intermediate tokens"""

        # Path without intermediates
        yield (source_token, target_token)

        # Path with each intermediate
        for middle in intermediate_tokens:
            if source_token.address == middle.address:
                # Skip WETH -> WETH -> USDC
                continue

            yield (source_token, middle, target_token)

    def format_path(self, route) -> str:
        str_path = [
            f"{route.source_token.symbol} ->",
        ]

        for token in route.path[1:-1]:
            str_path.append(f"{token.symbol} ->")

        str_path.append(
            f"{route.target_token.symbol}",
        )

        return " ".join(str_path)


def _fee_hook(
    source_token,
    target_token,
) -> tuple[int] | tuple[int, int]:
    """Guess supported fees for Uniswap v3 pairs.

    - Radically reduce the search space by using heurestics

    - 5 BPS is only available on well known pools, otherwise it is 30 bps or 1%
    """

    # fmt: off
    # #: 1 BPS = 100 units
    if (source_token.symbol == "WETH" and target_token.symbol == "USDC") or \
       (source_token.symbol == "USDC" and target_token.symbol == "WETH"):
        # 5 BPS is only enabled on
        return (500,)
    # fmt: on
    return (
        30 * 100,
        100 * 100,
    )


class UniswapV3Quoter(ValuationQuoter):
    """Handle Uniswap v3 quoters using QuoterV2 contract."""

    #: Quoter signature string for Multicall lib.
    #:
    #: https://basescan.org/address/0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a#code
    signature_string = "quoteExactInput(bytes,uint256)(uint256,uint160[],uint32[],uint256)"

    def __init__(
        self,
        quoter: Contract,
        debug: bool = False,
        # fee_tiers=(0.0030, 0.0005, 0.01),
        fee_hook=_fee_hook,
    ):
        super().__init__(debug=debug)
        assert isinstance(quoter, Contract)
        self.quoter = quoter
        # self.fee_tiers = [int(f * 1_000_000) for f in fee_tiers]
        self.fee_hook = _fee_hook

    def __repr__(self):
        return f"<UniswapV3QuoterV2({self.quoter.address})>"

    @classproperty
    def dex_hint(cls) -> str:
        return "uniswap-v3"

    def create_multicall_wrapper(self, route: Route, amount_in: int) -> ValuationMulticallWrapper:
        # If we need to optimise Python parsing speed, we can directly pass function selectors and pre-packed ABI
        path = encode_path(route.address_path, route.fees)

        bound_func = self.quoter.functions.quoteExactInput(
            path,
            amount_in,
        )
        return ValuationMulticallWrapper(
            quoter=self,
            route=route,
            amount_in=amount_in,
            debug=self.debug,
            call=bound_func,
        )

    def generate_routes(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
        amount: Decimal,
        debug: bool,
    ) -> Iterable[Route]:
        """Create routes we need to test on Uniswap v2"""

        for path, fees in self.get_path_combinations(
            source_token,
            target_token,
            intermediate_tokens,
        ):
            yield Route(
                quoter=self,
                path=path,
                fees=fees,
            )

    def handle_onchain_return_value(
        self,
        wrapper: ValuationMulticallWrapper,
        raw_return_value: any,
    ) -> Decimal | None:
        """Convert swapExactTokensForTokens() return value to tokens we receive"""

        route = wrapper.route
        #         returns (
        #             uint256 amountOut,
        #             uint160[] memory sqrtPriceX96AfterList,
        #             uint32[] memory initializedTicksCrossedList,
        #             uint256 gasEstimate
        #         );

        if raw_return_value == 0:
            # Not sure what's this?
            return None

        amount_out = int.from_bytes(raw_return_value[0:32])
        return route.target_token.convert_to_decimals(amount_out)

    def get_path_combinations(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
    ) -> Iterable[tuple[list[HexAddress], list[int]]]:
        """Generate Uniswap v3 swap paths and fee with all supported intermediate tokens"""

        # Path without intermediates
        fees = self.fee_hook(source_token, target_token)
        for fee in fees:
            yield (source_token, target_token), (fee,)

        # Path with each intermediate
        for middle in intermediate_tokens:
            fees_1 = self.fee_hook(source_token, middle)
            fees_2 = self.fee_hook(middle, target_token)
            for fee_1 in fees_1:
                for fee_2 in fees_2:
                    yield (source_token, middle, target_token), (fee_1, fee_2)

    def format_path(self, route) -> str:
        str_path = [
            f"{route.source_token.symbol} -({route.fees[0] // 100} BPS)->",
        ]

        for token in route.path[1:-1]:
            str_path.append(f"{token.symbol} -({route.fees[1] // 100} BPS)->")

        str_path.append(
            f"{route.target_token.symbol}",
        )

        return " ".join(str_path)


class NetAssetValueCalculator:
    """Calculate valuation of all vault spot assets, assuming we would sell them on Uniswap market sell or similar.

    - Query valuations using *only* onchain data / direct quoter smart contracts, no external indexers or services needed

    - Price impact and fees included

    - Brute forces all possible route combinations

    - Pack more RPC punch by using Multicall library

    .. note ::

        Early prototype code.

    Example:

    .. code-block::

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

        print(routes)

    Outputs:

    .. code-block:: text

        # Routes and their sell values:

                              Asset                                     Address        Balance                   Router Works  Value
         Path
         USDC                  USDC  0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913           0.35                            yes   0.35
         WETH -> USDC          WETH  0x4200000000000000000000000000000000000006       0.000000  UniswapV2Router02Quoter   yes   0.00
         DINO -> USDC          DINO  0x85E90a5430AF45776548ADB82eE4cD9E33B08077  547942.000069  UniswapV2Router02Quoter    no      -
         DINO -> WETH -> USDC  DINO  0x85E90a5430AF45776548ADB82eE4cD9E33B08077  547942.000069  UniswapV2Router02Quoter   yes  36.69

    """

    def __init__(
        self,
        web3: Web3,
        denomination_token: HexAddress | TokenDetails,
        intermediary_tokens: set[HexAddress | TokenDetails],
        quoters: set[ValuationQuoter],
        multicall: bool | None = None,
        block_identifier: BlockIdentifier = None,
        multicall_gas_limit=10_000_000,
        debug=False,
        batch_size=15,
        legacy_multicall=False,
    ):
        """Create a new NAV calculator.

        :param denomination_token:
            Value the portfolio in this token.

            E.g. USDC

        :param intermediary_tokens:
            When looking for sell routes, these are allowed tokens we can do three leg trades.

            E.g. WETH, USDT.

        :param quoters:
            Supported DEX quoters we can sell on.

        :param block_identifier:
            Block number for the valuation time.

        :param multicall:
            Use multicall to optimise RPC access.

            None = autodetect.

            True = force.

            False = disabled.

        :param multicall_gas_limit:
            Let's not explode our RPC node

        :param batch_size:
            Batch size to one Multicall RPC in the number of calls.

        :param debug:
            Unit test flag.

            Print out failed calldata to logging INFO,
            so you can inspect failed multicalls in Tenderly debugger.
        """
        self.web3 = web3
        self.chain_id = web3.eth.chain_id
        self.denomination_token = _convert_to_token_details(web3, self.chain_id, denomination_token)
        self.intermediary_tokens = {_convert_to_token_details(web3, self.chain_id, t) for t in intermediary_tokens}
        self.quoters = quoters
        self.multicall = multicall
        self.multicall_gas_limit = multicall_gas_limit
        self.debug = debug
        self.batch_size = batch_size
        self.legacy_multicall = legacy_multicall

        if block_identifier is None:
            block_identifier = get_almost_latest_block_number(web3)

        self.block_identifier = block_identifier

    def generate_routes_for_router(
        self,
        router: ValuationQuoter,
        portfolio: VaultPortfolio,
        buy=False,
    ) -> Iterable[Route]:
        """Create all potential routes we need to test to get quotes for a single asset.

        :param buy:
            Generate routes for buying: portfolio tokens present buy target.

            Otherwise generate routes for selling: portfolio tokens present tokens we want to get rid off.
        """
        for token_address, amount in portfolio.spot_erc20.items():
            if token_address == self.denomination_token.address_lower:
                # Reserve currency does not need to be valued in the reserve currency
                continue

            token = _convert_to_token_details(self.web3, self.chain_id, token_address)

            if buy:
                yield from router.generate_routes(
                    source_token=self.denomination_token,
                    target_token=token,
                    intermediate_tokens=self.intermediary_tokens,
                    amount=amount,
                    debug=self.debug,
                )
            else:
                yield from router.generate_routes(
                    source_token=token,
                    target_token=self.denomination_token,
                    intermediate_tokens=self.intermediary_tokens,
                    amount=amount,
                    debug=self.debug,
                )

    def calculate_market_sell_nav(
        self,
        portfolio: VaultPortfolio,
        allow_failed_routing=False,
    ) -> PortfolioValuation:
        """Calculate net asset value for each position.

                - Portfolio net asset value is the sum of positions

                - What is our NAV if we do market sell on DEXes for the whole portfolio now

                - Price impact included

                :param allow_failed_routing:
                    Raise an error if we cannot get a single route for some token
        s
                :return:
                    Map of token address -> valuation in denomiation token
        """
        assert portfolio.is_spot_only()
        assert portfolio.get_position_count() > 0, "Empty portfolio"
        logger.info("Calculating NAV for a portfolio with %d assets", portfolio.get_position_count())
        routes = [r for router in self.quoters for r in self.generate_routes_for_router(router, portfolio)]

        logger.info("Resolving total %d routes", len(routes))
        all_routes = self.fetch_onchain_valuations(routes, portfolio)

        if not allow_failed_routing:
            routes_per_token = defaultdict(list)
            for r, value in all_routes.items():
                routes_per_token[r.source_token].append((r, value))

            for token, routes in routes_per_token.items():
                if not any(t[1] is not None for t in routes):
                    new_line = "\n"
                    raise NoRouteFound(f"No single successful route for token {token}\nRoutes:\n{new_line.join(str(r[0]) + ':' + str(r[1]) for r in routes)}")

        logger.info("Got %d multicall results", len(all_routes))
        # Discard failed paths
        succeed_routes = {k: v for k, v in all_routes.items() if v is not None}

        logger.info("Found %d successful routes", len(succeed_routes))
        assert len(succeed_routes) > 0, "Could not find any viable routes for any token. We messed up smart contract calls badly?"

        best_result_by_token = self.resolve_best_valuations(portfolio.tokens, succeed_routes)

        # Reserve currency does not need to be traded
        if self.denomination_token.address_lower in portfolio.spot_erc20:
            best_result_by_token[self.denomination_token.address_lower] = portfolio.spot_erc20[self.denomination_token.address_lower]

        # Discard bad paths with None value
        valulation = PortfolioValuation(
            denomination_token=self.denomination_token,
            spot_valuations=best_result_by_token,
        )
        return valulation

    def resolve_best_valuations(
        self,
        input_tokens: set[HexAddress],
        routes: dict[Route, TokenAmount],
    ):
        """Any source token may have multiple paths. Pick one that gives the best amount out."""

        logger.info("Resolving best routes, %d tokens, %d routes", len(input_tokens), len(routes))
        # best_route_by_token: dict[TokenAddress, Route]
        best_result_by_token: dict[TokenAddress, TokenAmount] = LowercaseDict()
        for route, token_amount in routes.items():
            logger.info("Route %s got result %s", route, token_amount)
            if best_result_by_token.get(route.source_token.address, None) is None:
                # Initialise with 0.00
                best_result_by_token[route.source_token.address] = token_amount
            elif token_amount > best_result_by_token.get(route.source_token.address, 0):
                best_result_by_token[route.source_token.address] = token_amount

        # Validate all tokens got at least one path
        for token_address in input_tokens:
            if token_address == self.denomination_token.address_lower:
                # Cannot route reserve currency to itself
                continue

            if token_address not in best_result_by_token:
                token = fetch_erc20_details(self.web3, token_address)
                routes_tried = [r for r in routes.keys() if r.source_token.address == token_address]
                raise NoRouteFound(f"Token {token} did not get any valid DEX routing paths to calculate its current market value.\nRoutes tried: {routes_tried}")

        return best_result_by_token

    def do_multicall(
        self,
        calls: list[MulticallWrapper],
    ):
        """Multicall mess untangling."""
        if self.legacy_multicall:
            # Old bantg path.
            # Do not use.
            # Only headche.
            multicall = Multicall(
                calls=[c.create_multicall() for c in calls],
                block_id=self.block_identifier,
                _w3=self.web3,
                require_success=False,
                gas_limit=self.multicall_gas_limit,
            )
            batched_result = multicall()
            return batched_result
        else:
            multicall_contract = get_multicall_contract(
                self.web3,
                block_identifier=self.block_identifier,
            )
            # return call_multicall_debug_single_thread(
            #     multicall_contract,
            #     calls=calls,
            #     block_identifier=self.block_identifier,
            # )

            return call_multicall_batched_single_thread(
                multicall_contract,
                calls=calls,
                block_identifier=self.block_identifier,
                batch_size=self.batch_size,
            )

    def fetch_onchain_valuations(
        self,
        routes: list[Route],
        portfolio: VaultPortfolio,
        legacy=False,
    ) -> dict[Route, TokenAmount]:
        """Use multicall to make calls to all of our quoters.

        - Does not handle reserve currency, as this never has any route to itself

        :return:
            Map routes -> amount out token amounts with this route
        """
        multicall = self.multicall
        if multicall is None:
            logger.info("Autodetecting multicall")
            multicall = is_mainnet_fork(self.web3)

        raw_balances = portfolio.get_raw_spot_balances(self.web3)

        logger.info("fetch_onchain_valuations(), %d routes, multicall is %s", len(routes), multicall)
        calls = [r.quoter.create_multicall_wrapper(r, raw_balances[r.source_token.address]) for r in routes]

        logger.info("Processing %d Multicall Calls", len(calls))

        if multicall:
            return self.do_multicall(calls)
        else:
            # Fallback not supported yet
            raise NotImplementedError()

    def try_swap_paths(
        self,
        routes: list[Route],
        portfolio: VaultPortfolio,
    ) -> dict[Route, TokenAmount]:
        """Use multicall to try all possible swap paths for tokens.

        - Find the best buy options

        - Assume :py:attr:`VaultPortfolio.spot_erc20` contains token amounts we want to buy

        :return:
            Map routes -> amount out token amounts with this route
        """
        multicall = self.multicall
        if multicall is None:
            logger.info("Autodetecting multicall")
            multicall = is_mainnet_fork(self.web3)

        web3 = self.web3
        chain_id = web3.eth.chain_id

        denomination_token = self.denomination_token
        tokens: dict[HexAddress, TokenDetails] = {address: fetch_erc20_details(web3, address, chain_id) for address in portfolio.tokens}
        raw_balances = LowercaseDict(**{address: denomination_token.convert_to_raw(portfolio.spot_erc20[address]) for address, token in tokens.items()})

        logger.info(
            "try_swap_paths(), %d routes, %d quoters, multicall is %s",
            len(routes),
            len(self.quoters),
            multicall,
        )

        calls = [r.quoter.create_multicall_wrapper(r, raw_balances[r.target_token.address]) for r in routes]

        logger.info("Processing %d Multicall Calls", len(calls))

        if multicall:
            return self.do_multicall(calls)
        else:
            # Fallback not supported yet
            raise NotImplementedError()

    def create_route_diagnostics(
        self,
        portfolio: VaultPortfolio,
    ) -> pd.DataFrame:
        """Create a route diagnotics table.

        - Show all routes generated for the portfolio

        - Flag routes that work

        - Show values of each portfolio position if sold with the route

        Outputs:

        .. code-block:: text

                                 Asset                                     Address        Balance                   Router Works  Value
            Path
            USDC                  USDC  0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913           0.35                            yes   0.35
            WETH -> USDC          WETH  0x4200000000000000000000000000000000000006       0.000000  UniswapV2Router02Quoter   yes   0.00
            DINO -> USDC          DINO  0x85E90a5430AF45776548ADB82eE4cD9E33B08077  547942.000069  UniswapV2Router02Quoter    no      -
            DINO -> WETH -> USDC  DINO  0x85E90a5430AF45776548ADB82eE4cD9E33B08077  547942.000069  UniswapV2Router02Quoter   yes  36.69

        :return:
            Human-readable DataFrame.

            Indexed by asset.
        """
        routes = [r for router in self.quoters for r in self.generate_routes_for_router(router, portfolio)]
        sell_prices = self.fetch_onchain_valuations(routes, portfolio)

        data = []

        reserve_balance = portfolio.spot_erc20.get(self.denomination_token.address, 0)

        if reserve_balance:
            # Handle case where we cannot route reserve balance to itself
            data.append(
                {
                    "DEX": "reserve",
                    "Path": self.denomination_token.symbol,
                    # "Asset": self.denomination_token.symbol,
                    # "Address": self.denomination_token.address,
                    "Balance": f"{reserve_balance:,.2f}",
                    # "Router": "",
                    "Works": "yes",
                    "Value": f"{reserve_balance:,.2f}",
                }
            )

        for route in routes:
            out_balance = sell_prices[route]

            if out_balance is not None:
                formatted_balance = f"{out_balance:,.2f}"
            else:
                formatted_balance = "-"

            data.append(
                {
                    "DEX": route.quoter.dex_hint,
                    "Path": route.quoter.format_path(route),
                    # "Asset": route.source_token.symbol,
                    # "Address": route.source_token.address,
                    "Balance": f"{portfolio.spot_erc20[route.source_token.address]:.6f}",
                    "Works": "yes" if out_balance is not None else "no",
                    "Value": formatted_balance,
                }
            )

        df = pd.DataFrame(data)
        df = df.sort_values(by=["Path", "DEX"])
        return df

    def find_swap_routes(self, portfolio: VaultPortfolio, buy=True) -> SwapMatrix:
        """Find the best routes to buy tokens."""

        assert portfolio.is_spot_only()
        assert portfolio.get_position_count() > 0, "Empty portfolio"
        logger.info("find_swap_routes(), portfolio with %d assets", portfolio.get_position_count())
        routes = [r for router in self.quoters for r in self.generate_routes_for_router(router, portfolio, buy=buy)]
        logger.info("Resolving total %d routes", len(routes))
        all_route_results = self.try_swap_paths(routes, portfolio)
        results_by_token = defaultdict(list)

        for r, amount in all_route_results.items():
            results_by_token[r.target_token].append((r, amount))

        def _get_route_priorisation_sort_key(route_amount_tuple):
            amount = route_amount_tuple[1]
            if amount is None:
                # router failed, sort to end
                return Decimal(0)

            return amount

        # Make so that the best result (most tokens bought) is the first of all tried results
        results_by_token = {token: sorted(routes, key=_get_route_priorisation_sort_key, reverse=True) for token, routes in results_by_token.items()}

        return SwapMatrix(
            results=all_route_results,
            best_results_by_token=results_by_token,
        )


def _convert_to_token_details(
    web3: Web3,
    chain_id: int,
    token_or_address: HexAddress | TokenDetails,
) -> TokenDetails:
    if isinstance(token_or_address, TokenDetails):
        return token_or_address
    return fetch_erc20_details(web3, token_or_address, chain_id=chain_id)

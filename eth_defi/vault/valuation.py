"""Net asset valuation calculations for token portfolios and vaults.

- Calculate the value of vault portfolio using only onchain data,
  available from JSON-RPC

- See :py:class:`NetAssetValueCalculator` for usage

"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Any, TypeAlias

import pandas as pd
from eth_typing import HexAddress, BlockIdentifier
from multicall import Call, Multicall
from safe_eth.eth.constants import NULL_ADDRESS
from web3 import Web3
from web3.contract import Contract


from eth_defi.provider.anvil import is_mainnet_fork
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails, fetch_erc20_details, TokenAddress
from eth_defi.vault.base import VaultPortfolio

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



@dataclass(slots=True, frozen=True)
class Route:
    """One potential swap path.

    - Present one potential swap path between source and target

    - Routes can contain any number of intermediate tokens in the path

    - Used to ABI encode for multicall calls
    """
    source_token: TokenDetails
    target_token: TokenDetails
    quoter: "ValuationQuoter"
    path: tuple[HexAddress, HexAddress] | tuple[HexAddress, HexAddress, HexAddress]

    def __repr__(self):
        return f"<Route {self.path} using quoter {self.quoter}>"

    def __hash__(self) -> int:
        """Unique hash for this instance"""
        return hash((self.quoter, self.source_token.address, self.path))

    def __eq__(self, other: "Route") -> int:
        return self.source_token == other.source_token and self.path == other.path and self.contract_address == other.contract_address

    @property
    def function_signature_string(self) -> str:
        return self.signature[0]

    @property
    def token(self) -> TokenDetails:
        return self.source_token


@dataclass(slots=True, frozen=True)
class MulticallWrapper:
    """Wrap the undertlying Multicall with diagnostics data.

    - Because the underlying Multicall lib is not powerful enough.

    - And we do not have time to fix it
    """

    quoter: "ValuationQuoter"
    route: Route
    amount_in: int
    signature_string: str
    contract_address: HexAddress
    signature: list[Any]
    debug: bool = False  # Unit test flag

    def __repr__(self):
        return f"<MulticallWrapper {self.amount_in} for {self.signature_string}>"

    def create_multicall(self) -> Call:
        """Create underlying call about."""
        call = Call(self.contract_address, self.signature, [(self.route, self)])
        return call

    def get_data(self) -> bytes:
        """Return data field for the transaction payload"""
        call = self.create_multicall()
        data = call.data
        return data

    def get_selector(self) -> bytes:
        """Get 4-bytes Solidity function selector."""
        call = self.create_multicall()
        return call.signature.fourbyte

    def get_args(self) -> list[Any]:
        """Get undecoded Solidity arguments passed to the underlying func."""
        return self.signature[1:]

    def multicall_callback(self, succeed: bool, raw_return_value: Any) -> TokenAmount | None:
        """Convert the raw Solidity function call result to a denominated token amount.

        - Multicall library callback

        :return:
            The token amount in the reserve currency we get on the market sell.

            None if this path was not supported (Solidity reverted).
        """
        if not succeed:
            # Avoid expensive logging if we do not need it
            if self.debug:
                # Print calldata so we can copy-paste it to Tenderly for symbolic debug stack trace
                data = self.get_data()
                call = self.create_multicall()
                logger.info("Path did not success: %s on %s, selector %s",
                    self,
                    self.signature_string,
                    call.signature.fourbyte.hex(),
                )
                logger.info("Arguments: %s", self.signature[1:])
                logger.info(
                    "Contract: %s\nCalldata: %s",
                    self.contract_address,
                    data.hex()
                )
            return None

        try:
            token_amount = self.quoter.handle_onchain_return_value(
                self,
                raw_return_value,
            )
            return token_amount

        except Exception as e:
            logger.error(
                "Router handler failed %s for return value %s",
                self.quoter,
                raw_return_value,
            )
            raise e #  0.0000673


        if self.debug:
            logger.info(
            "Route succeed: %s, we can sell %s for %s reserve currency",
                self,
                self.route,
                token_amount
            )

    def create_tx_data(self, from_= NULL_ADDRESS) -> dict:
        """Create payload for eth_call."""
        return {
            "from": NULL_ADDRESS,
            "to": self.contract_address,
            "data": self.get_data(),
        }

    def get_debug_string(self) -> str:
        """Help why we fail."""
        data = self.get_data()
        return f"Could not execute {self.signature_string}.\nAddress: {self.contract_address}\nSelector: {self.get_selector().hex()}\nArgs: {self.get_args()}\nData: {data.hex()}"

    def __call__(
        self,
        success: bool,
        raw_return_value: Any
    ):
        """Called by Multicall lib"""
        try:
            return self.multicall_callback(success, raw_return_value)
        except Exception as e:
            logger.error(
                "Could not decode multicall result, success %s, %s=%s",
                 success,
                 self.route,
                 raw_return_value,
                exc_info=e,
            )
            raise


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
    def create_multicall_wrapper(self, route: Route, amount_in: int) -> MulticallWrapper:
        pass



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

    def create_multicall_wrapper(self, route: Route, amount_in: int) -> MulticallWrapper:
        # If we need to optimise Python parsing speed, we can directly pass function selectors and pre-packed ABI

        signature = [
            self.signature_string,
            amount_in,
            route.path,
        ]

        return MulticallWrapper(
            quoter=self,
            route=route,
            amount_in=amount_in,
            debug=self.debug,
            signature_string=self.signature_string,
            contract_address=self.swap_router_v2.address,
            signature=signature,
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
                source_token=source_token,
                target_token=target_token,
                quoter=self,
                path=path,
            )

    def handle_onchain_return_value(
        self,
        wrapper: MulticallWrapper,
        raw_return_value: any,
    ) -> Decimal | None:
        """Convert swapExactTokensForTokens() return value to tokens we receive"""
        route = wrapper.route
        target_token_out = raw_return_value[-1]
        return route.target_token.convert_to_decimals(target_token_out)

    def get_path_combinations(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
    ) -> Iterable[tuple[HexAddress]]:
        """Generate Uniswap v2 swap paths with all supported intermediate tokens"""

        # Path without intermediates
        yield (source_token.address, target_token.address)

        # Path with each intermediate
        for middle in intermediate_tokens:

            if source_token.address == middle.address:
                # Skip WETH -> WETH -> USDC
                continue

            yield (source_token.address, middle.address, target_token.address)


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
        multicall: bool|None=None,
        block_identifier: BlockIdentifier = None,
        multicall_gas_limit=10_000_000,
        debug=False,
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

        if block_identifier is None:
            block_identifier = get_almost_latest_block_number(web3)

        self.block_identifier = block_identifier

    def generate_routes_for_router(self, router: ValuationQuoter, portfolio: VaultPortfolio) -> Iterable[Route]:
        """Create all potential routes we need to test to get quotes for a single asset."""
        for token_address, amount in portfolio.spot_erc20.items():

            if token_address == self.denomination_token.address:
                # Reserve currency does not need to be valued in the reserve currency
                continue

            token = _convert_to_token_details(self.web3, self.chain_id, token_address)
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
    ) -> PortfolioValuation:
        """Calculate net asset value for each position.

        - Portfolio net asset value is the sum of positions

        - What is our NAV if we do market sell on DEXes for the whole portfolio now

        - Price impact included
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

        logger.info("Got %d multicall results", len(all_routes))
        # Discard failed paths
        succeed_routes = {k: v for k, v in all_routes.items() if v is not None}

        logger.info("Found %d successful routes", len(succeed_routes))
        assert len(succeed_routes) > 0, "Could not find any viable routes for any token. We messed up smart contract calls badly?"

        best_result_by_token = self.resolve_best_valuations(portfolio.tokens, succeed_routes)

        # Reserve currency does not need to be traded
        if self.denomination_token.address in portfolio.spot_erc20:
            best_result_by_token[self.denomination_token.address] = portfolio.spot_erc20[self.denomination_token.address]

        # Discard bad paths with None value
        valulation = PortfolioValuation(
            denomination_token=self.denomination_token,
            spot_valuations=best_result_by_token,
        )
        return valulation

    def resolve_best_valuations(
        self,
        input_tokens: set[HexAddress],
        routes: dict[Route, TokenAmount]
    ):
        """Any source token may have multiple paths. Pick one that gives the best amount out."""

        logger.info("Resolving best routes, %d tokens, %d routes", len(input_tokens), len(routes))
        # best_route_by_token: dict[TokenAddress, Route]
        best_result_by_token: dict[TokenAddress, TokenAmount] = {}
        for route, token_amount in routes.items():
            logger.info("Route %s got result %s", route, token_amount)

            if best_result_by_token.get(route.source_token.address, None) is None:
                # Initialise with 0.00
                best_result_by_token[route.source_token.address] = token_amount
            elif token_amount > best_result_by_token.get(route.source_token.address, 0):
                best_result_by_token[route.source_token.address] = token_amount

        # Validate all tokens got at least one path
        for token_address in input_tokens:

            if token_address == self.denomination_token.address:
                # Cannot route reserve currency to itself
                continue

            if token_address not in best_result_by_token:
                token = fetch_erc20_details(self.web3, token_address)
                raise NoRouteFound(f"Token {token} did not get any valid DEX routing paths to calculate its current market value")

        return best_result_by_token

    def fetch_onchain_valuations(
        self,
        routes: list[Route],
        portfolio: VaultPortfolio,
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
        calls = [r.quoter.create_multicall_wrapper(r, raw_balances[r.source_token.address]).create_multicall() for r in routes]

        logger.info("Processing %d Multicall Calls", len(calls))

        if multicall:
            multicall = Multicall(
                calls=calls,
                block_id=self.block_identifier,
                _w3=self.web3,
                require_success=False,
                gas_limit=self.multicall_gas_limit,
            )
            batched_result = multicall()
            return batched_result
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
x
            Indexed by asset.
        """
        routes = [r for router in self.quoters for r in self.generate_routes_for_router(router, portfolio)]
        sell_prices = self.fetch_onchain_valuations(routes, portfolio)

        data = []

        reserve_balance = portfolio.spot_erc20.get(self.denomination_token.address, 0)

        if reserve_balance:
            # Handle case where we cannot route reserve balance to itself
            data.append({
                "Path": self.denomination_token.symbol,
                "Asset": self.denomination_token.symbol,
                "Address": self.denomination_token.address,
                "Balance": f"{reserve_balance:,.2f}",
                "Router": "",
                "Works": "yes",
                "Value": f"{reserve_balance:,.2f}",
            })

        for route in routes:

            out_balance = sell_prices[route]

            if out_balance is not None:
                formatted_balance = f"{out_balance:,.2f}"
            else:
                formatted_balance = "-"

            data.append({
                "Path": _format_symbolic_path_uniswap_v2(self.web3, route),
                "Asset": route.source_token.symbol,
                "Address": route.source_token.address,
                "Balance": f"{portfolio.spot_erc20[route.source_token.address]:.6f}",
                "Router": route.quoter.__class__.__name__,
                "Works": "yes" if out_balance is not None else "no",
                "Value": formatted_balance,
            })

        df = pd.DataFrame(data)
        df = df.set_index("Path")
        return df


def _convert_to_token_details(
    web3: Web3,
    chain_id: int,
    token_or_address: HexAddress | TokenDetails,
) -> TokenDetails:
    if isinstance(token_or_address, TokenDetails):
        return token_or_address
    return fetch_erc20_details(web3, token_or_address, chain_id=chain_id)


def _format_symbolic_path_uniswap_v2(web3, route: Route) -> str:
    """Get human-readable route path line."""

    chain_id = web3.eth.chain_id

    str_path = [
        f"{route.source_token.symbol} ->"
    ]

    for step in route.path[1:-1]:
        token = fetch_erc20_details(web3, step, chain_id=chain_id)
        str_path.append(f"{token.symbol} ->")

    str_path.append(
        f"{route.target_token.symbol}"
    )

    return " ".join(str_path)

"""Net asset valuation calculations.

- Calcualte the value of vault portfolio using only onchain data

"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Any, TypeAlias

from eth_typing import HexAddress, BlockIdentifier
from multicall import Call, Multicall
from safe_eth.eth.constants import NULL_ADDRESS
from web3 import Web3
from web3.contract import Contract


from eth_defi.provider.anvil import is_mainnet_fork
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails, fetch_erc20_details, TokenAddress
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE
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
    router: "ValuationQuoter"
    path: tuple[HexAddress]
    contract_address: HexAddress
    signature: list[Any]
    extra_data: Any | None = None

    def __hash__(self) -> int:
        return hash(self.router, self.source_token.address, self.path)

    def __eq__(self, other: "Route") -> int:
        return self.source_token == other.source_token and self.path == other.path and self.contract_address == other.contract_address

    @property
    def token(self) -> TokenDetails:
        return self.path[0]

    def create_multicall(self) -> Call:
        # If we need to optimise Python parsing speed, we can directly pass function selectors and pre-packed ABI
        return Call(self.contract_address, self.signature, [(self.source_token.address, self.handle_onchain_return_value)])

    def handle_onchain_return_value(self, succeed: bool, raw_return_value: Any) -> TokenAmount | None:
        """Convert the rwa Solidity function call result to a denominated token amount.

        - Multicall library callback

        :return:
            The token amount in the reserve currency we get on the market sell.

            None if this path was not supported (Solidity reverted).
        """

        if not succeed:
            return None

        return self.router.handle_onchain_return_value(
            self,
            raw_return_value,
        )


class ValuationQuoter(ABC):
    """Handle asset valuation on a specific DEX/quoter.

    - Takes in source and target tokens as input and generate all routing path combinations

    - Creates routes to a specific DEX

    - Each DEX has its own quoter contract we need to integrate

    - Resolves the onchain Solidity function return value to a token amount we get
    """

    @abstractmethod
    def generate_routes(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
        amount: Decimal,
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



class UniswapV2Router02Quoter(ValuationQuoter):
    """Handle Uniswap v2 quoters using Router02 contract.

    https://docs.uniswap.org/contracts/v2/reference/smart-contracts/router-02#swapexacttokensfortokens
    """

    #: Router02 function we use to calculate outs from token ins
    func_string = "swapExactTokensForTokens(uint,uint,address[],address,uint)(uint[])"

    def __init__(
        self,
        router_v2: Contract,
    ):
        assert isinstance(router_v2, Contract)
        self.router_v2 = router_v2

    def generate_routes(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
        amount: Decimal,
    ) -> Iterable[Route]:
        """Create routes we need to test on Uniswap v2"""

        for path in self.get_path_combinations(
            source_token,
            target_token,
            intermediate_tokens,
        ):
            # https://docs.uniswap.org/contracts/v2/reference/smart-contracts/router-02#swapexacttokensfortokens
            signature = [
                self.func_string,
                source_token.convert_to_raw(amount),
                0,
                path,
                NULL_ADDRESS,
                FOREVER_DEADLINE,
            ]
            yield Route(
                contract_address=self.router_v2.address,
                source_token=source_token,
                target_token=target_token,
                router=self,
                path=path,
                signature=signature,
            )

    def handle_onchain_return_value(
        self,
        route: Route,
        raw_return_value: any,
    ) -> Decimal | None:
        """Convert swapExactTokensForTokens() return value to tokens we receive"""
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
            yield (source_token.address, middle.address, target_token.address)


class NetAssetValueCalculator:
    """Calculate valuation of all vault spot assets, assuming we would sell them on Uniswap market sell or similar.

    - Query valuations using onchain data / direct quoter smart contracts

    - Price impact and fees included

    - Pack more RPC punch by using Multicall library
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
    ):
        """Create a new NAV calculator.

        :param multicall:
            Use multicall to optimise RPC access.

            None = autodetect.

            True = force.

            False = disabled.
        """
        self.web3 = web3
        self.chain_id = web3.eth.chain_id
        self.denomination_token = _convert_to_token_details(web3, self.chain_id, denomination_token)
        self.intermediary_tokens = {_convert_to_token_details(web3, self.chain_id, t) for t in intermediary_tokens}
        self.quoters = quoters
        self.multicall = multicall
        self.multicall_gas_limit = multicall_gas_limit

        if block_identifier is None:
            block_identifier = get_almost_latest_block_number(web3)

        self.block_identifier = block_identifier

    def generate_routes_for_router(self, router: ValuationQuoter, portfolio: VaultPortfolio) -> Iterable[Route]:
        """Create all potential routes we need to test to get quotes for a single asset."""
        for token_address, amount in portfolio.spot_erc20.items():
            token = _convert_to_token_details(self.web3, self.chain_id, token_address)
            yield from router.generate_routes(
                source_token=token,
                target_token=self.denomination_token,
                intermediate_tokens=self.intermediary_tokens,
                amount=amount,
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
        all_routes = self.fetch_onchain_valuations(routes)

        # Discard failed paths
        succeed_routes = {k: v for k, v in all_routes.items() if v is not None}

        assert len(succeed_routes) > 0, "Could not find any viable routes for any token. We messed up smart contract calls badly?"

        best_result_by_token = self.resolve_best_valuations(succeed_routes)

        # Discard bad paths with None value
        valulation = PortfolioValuation(
            denomination_token=self.denomination_token,
            spot_valuations=best_result_by_token,
        )
        return valulation

    def resolve_best_valuations(
        self,
        input_tokens: set[TokenDetails],
        routes: dict[Route, TokenAmount]
    ):
        """Any source token may have multiple paths. Pick one that gives the best amount out."""

        logger.info("Resolving best routes, %d tokens, %d routes", len(input_tokens), len(routes))
        # best_route_by_token: dict[TokenAddress, Route]
        best_result_by_token: dict[TokenAddress, TokenAmount] = {}
        for route, token_amount in routes.items():
            if token_amount > best_result_by_token.get(route.source_token.address, 0):
                best_result_by_token[route.source_token.address] = token_amount

        # Validate all tokens got at least one path
        for token in input_tokens:
            if token.address not in best_result_by_token:
                raise NoRouteFound(f"Token {token} did not get any valid DEX routing paths to calculate its current market value")

        return best_result_by_token

    def fetch_onchain_valuations(
        self,
        routes: list[Route],
    ) -> dict[Route, TokenAmount]:
        """Use multicall to make calls to all of our quoters.

        :return:
            Map routes -> amount out token amounts with this route
        """
        multicall = self.multicall
        if multicall is None:
            logger.info("Autodetecting multicall")
            multicall = is_mainnet_fork(self.web3)

        logger.info("fetch_onchain_valuations(), %d routes, multicall is %s", len(routes), multicall)
        calls = [r.create_multicall() for r in routes]

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
            # Fallbaack
            raise NotImplementedError()


def _convert_to_token_details(
    web3: Web3,
    chain_id: int,
    token_or_address: HexAddress | TokenDetails,
) -> TokenDetails:
    if isinstance(token_or_address, TokenDetails):
        return token_or_address
    return fetch_erc20_details(web3, token_or_address, chain_id=chain_id)


def _get_signature():
    pass
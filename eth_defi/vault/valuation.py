"""Net asset valuation calculations.

- Calcualte the value of vault portfolio using only onchain data

"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Callable, Any

from eth_typing import HexAddress, BlockIdentifier
from multicall import Call, Multicall
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.provider.anvil import is_mainnet_fork
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.vault.base import VaultPortfolio

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PortfolioValuation:
    """Valuation calulated for a portfolio.

    See :py:class:`eth_defi.vault.base.VaultPortfolio` for the portfolio itself.
    """

    #: The reserve currency of this vault
    denomination_token: TokenDetails

    #: Individual spot valuations
    spot_valuations: dict[HexAddress, Decimal]

    def get_total_equity(self) -> Decimal:
        """How much we value this portfolio in the :py:attr:`denomination_token`"""
        return sum(self.spot_valuations.values())


@dataclass(slots=True, frozen=True)
class Route:
    """A smart contract call """
    token: TokenDetails
    router: "ValuationQuoter"
    func: ContractFunction
    args: tuple
    handler: Callable
    extra_data: Any | None = None

    def create_multicall(self) -> Call:
        sig = ""
        sig_and_args = [sig, address]
        return Call(self.contract.address, sig_and_args, [(self.token.address, self.handler)])

    def handle_response(self, raw_return_value) -> Decimal:
        """Convert the rwa Solidity function call result to a denominated token amount."""
        return self.handler(
            self,
            raw_return_value,
        )


class ValuationQuoter(ABC):
    """Handle asset valuation on a specific DEX/quoter"""

    @abstractmethod
    def generate_routes(
        self,
        source_token: TokenDetails,
        target_token: TokenDetails,
        intermediate_tokens: set[TokenDetails],
        amount: Decimal,
    ) -> Iterable[Route]:
        pass

    @abstractmethod
    def handle_response(
        self,
        route: Route,
        raw_return_value: any,
    ):
        pass


class UniswapV2Router02Quoter(ValuationQuoter):
    """Handle Uniswap v2 quoters using Router02 contract.

    https://docs.uniswap.org/contracts/v2/reference/smart-contracts/router-02#swapexacttokensfortokens
    """

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
        pass

    def handle_response(
        self,
        route: Route,
        raw_return_value: any,
    ):
        pass


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
        for token_address, amount in portfolio.spot_erc20:
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
    ) -> dict[HexAddress, Decimal]:
        """Calculate net asset value for each position.

        - Portfolio net asset value is the sum of positions

        - What is our NAV if we do market sell on DEXes for the whole portfolio now

        - Price impact included
s
        :return:
            Map of token address -> valuation in denomiation token
        """
        assert portfolio.is_spot_only()
        logger.info("Calculating NAV for a portfolio with %d assets", portfolio.get_position_count())
        routes = [r for router in self.quoters for r in self.generate_routes_for_router(router, portfolio)]
        return self.fetch_onchain_valuations(routes)

    def fetch_onchain_valuations(
        self,
        routes: list[Route],
    ) -> PortfolioValuation:
        """Use multicall to make calls to all of our quoters.

        :return:

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


def calculate_nav_on_market_sell(
    portfolio: VaultPortfolio,
    quoter: Contract,
    valuation_asset: HexAddress,
    routers: set[Valu]
):
    """Calculate valuation of all vault spot assets, assuming we would sell them on Uniswap market sell.

    :param portfolio:
        The gathered portfolio of current assets

    :param quoter:
        Uniswap QuoterV2 smart contract.

    :param intermedia_token:
        The supported intermediate token if we cannot do direct market sell.

    :param valuation_asset:
        The asset in which we value the portfolio.

        E.g. `USDC`.
    """

    calls = []
    for token in portfolio.spot_erc20:
        pass

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
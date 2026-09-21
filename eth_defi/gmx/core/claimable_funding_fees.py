"""
GMX claimable funding fees data retrieval module.

GMX V2 accrues funding fees continuously per second into a per-``(market,
token, account)`` balance in the ``DataStore`` under the
``CLAIMABLE_FUNDING_AMOUNT`` key. A position holder releases that balance by
calling ``ExchangeRouter.claimFundingFees(markets, tokens, receiver)``.

Unlike :mod:`eth_defi.gmx.core.claimable_fees` -- which reads the market-level
``CLAIMABLE_FEE_AMOUNT`` LP protocol-fee pool -- this module reads the
**per-account** trader funding receipts that ``claimFundingFees`` actually
consumes. The claimable amount is keyed by ``(market, token, account)``; the
``account`` is the position holder (an EOA, or a Lagoon vault's Gnosis Safe),
not the transaction signer.

The reader mirrors :class:`eth_defi.gmx.core.claimable_fees.GetClaimableFees`
for multicall batching and USD valuation, but queries the per-account funding
key and returns the ``(market, token)`` pairs a claim transaction consumes.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from eth_typing import HexAddress
from eth_utils import keccak, to_checksum_address

from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, read_multicall_chunked
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import PRECISION
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.keys import claimable_funding_amount_key
from eth_defi.gmx.types import MarketData

logger = logging.getLogger(__name__)


#: Marker for the human-readable parameter name returned by :meth:`GetClaimableFundingFees.get_claimable_funding_fees`.
CLAIMABLE_FUNDING_FEES_PARAMETER = "total_funding_fees"


class GetClaimableFundingFees(GetData):
    """Per-account claimable GMX V2 funding fees with multicall batching.

    Reads ``DataStore.getUint(claimable_funding_amount_key(market, token,
    account))`` for every available market and both the long and short token,
    returning the nonzero receipts plus a USD valuation.

    The ``account`` is the position holder whose funding accrued. When claiming
    through a Lagoon vault this is the vault's Gnosis Safe address, because
    ``TradingStrategyModuleV0.performCall()`` reaches the target through the
    Safe (``msg.sender`` seen by ``ExchangeRouter.claimFundingFees``).

    Example::

        from eth_defi.gmx.config import GMXConfig
        from eth_defi.gmx.core.claimable_funding_fees import GetClaimableFundingFees

        config = GMXConfig(web3, user_wallet_address=safe_address)
        reader = GetClaimableFundingFees(config, safe_address)
        markets, tokens = reader.get_claim_args()
    """

    def __init__(
        self,
        config: GMXConfig,
        account: HexAddress | str,
        *,
        filter_swap_markets: bool = True,
    ):
        """Initialize the per-account claimable funding fees reader.

        :param config:
            GMXConfig instance containing chain and network info.

        :param account:
            The funding-fee account whose ``CLAIMABLE_FUNDING_AMOUNT`` balances
            are read. This is the position holder (EOA or vault Safe), i.e. the
            ``msg.sender`` that will call ``claimFundingFees``.

        :param filter_swap_markets:
            Whether to filter out swap markets from results.
        """
        super().__init__(config, filter_swap_markets)
        self.account = to_checksum_address(account)
        self.oracle_prices = OraclePrices(chain=config.chain).get_recent_prices()

    def _get_data_processing(self) -> MarketData:
        """Implementation of the abstract method from the :class:`GetData` base class.

        :return: Claimable funding fees summary dictionary.
        """
        return self.get_claimable_funding_fees()

    def get_claimable_funding_fees(self) -> MarketData:
        """Get the total claimable funding fees for the configured account.

        :return:
            Dictionary with ``total_fees`` (USD), ``parameter`` and the
            per-market breakdown under ``markets``.
        """
        market_fees = self.get_per_market_claimable_funding_fees()
        total_fees = sum(fee_data["total"] for fee_data in market_fees.values())

        return {
            "total_fees": total_fees,
            "parameter": CLAIMABLE_FUNDING_FEES_PARAMETER,
            "markets": market_fees,
        }

    def get_per_market_claimable_funding_fees(self) -> dict[str, dict[str, Any]]:
        """Get the per-market claimable funding fees for the configured account.

        Reads both the long and short token receipts for every available market
        in a single batched multicall and USD-values them via the GMX oracle.

        :return:
            Dictionary keyed by market symbol. Each value contains the market
            address, the long/short token addresses, the raw (wei) claimable
            amounts, and the long/short/total USD value.
        """
        logger.debug("GMX v2 Claimable Funding Fees using Multicall (account=%s)", self.account)

        available_markets = self.markets.get_available_markets()
        if not available_markets:
            logger.warning("No markets available")
            return {}

        multicall_results = self._run_multicalls(available_markets)
        logger.debug("Processed multicalls for %s markets", len(multicall_results))

        market_fees: dict[str, dict[str, Any]] = {}
        for market_key in available_markets:
            if market_key not in multicall_results:
                logger.warning("No multicall results for market %s", market_key)
                continue

            try:
                market_fees[self.markets.get_market_symbol(market_key)] = self._process_market(market_key, multicall_results[market_key])
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                logger.error("Failed to process market %s: %s", market_key, e)
                continue

        return market_fees

    def get_claim_pairs(self) -> list[tuple[HexAddress, HexAddress, int]]:
        """Get the nonzero ``(market, token, raw_amount)`` receipts to claim.

        Only entries with a positive raw balance are returned, so the result is
        exactly the set a ``claimFundingFees`` call needs.

        :return:
            List of ``(market_address, token_address, raw_amount)`` tuples.
        """
        pairs: list[tuple[HexAddress, HexAddress, int]] = []
        for fee_data in self.get_per_market_claimable_funding_fees().values():
            if fee_data["long_raw"] > 0 and fee_data["long_token"]:
                pairs.append((fee_data["market"], fee_data["long_token"], fee_data["long_raw"]))
            if fee_data["short_raw"] > 0 and fee_data["short_token"]:
                pairs.append((fee_data["market"], fee_data["short_token"], fee_data["short_raw"]))
        return pairs

    def get_claim_args(self) -> tuple[list[HexAddress], list[HexAddress]]:
        """Get the parallel ``markets`` and ``tokens`` arrays for a claim.

        These are the two leading arguments of
        ``ExchangeRouter.claimFundingFees(markets, tokens, receiver)``.

        :return:
            Tuple of ``(markets, tokens)`` lists, aligned by index.
        """
        pairs = self.get_claim_pairs()
        markets = [market for market, _token, _raw in pairs]
        tokens = [token for _market, token, _raw in pairs]
        return markets, tokens

    def _run_multicalls(self, available_markets: dict[str, Any]) -> dict[str, dict[str, EncodedCallResult]]:
        """Execute the batched ``DataStore.getUint`` multicalls for all markets.

        :param available_markets: Market address to metadata mapping.
        :return: Nested mapping of ``market_key`` -> ``token_type`` -> call result.
        """
        encoded_calls = list(self.generate_all_multicalls(available_markets))
        logger.debug("Generated %s multicall requests", len(encoded_calls))

        web3_factory = TunedWeb3Factory(rpc_config_line=self.config.web3.provider.endpoint_uri)

        multicall_results: dict[str, dict[str, EncodedCallResult]] = defaultdict(dict)
        for call_result in read_multicall_chunked(
            chain_id=self.config.web3.eth.chain_id,
            web3factory=web3_factory,
            calls=encoded_calls,
            block_identifier="latest",
            progress_bar_desc="Loading claimable funding fees data",
            max_workers=5,
        ):
            market_key = call_result.call.extra_data["market_key"]
            token_type = call_result.call.extra_data["token_type"]
            multicall_results[market_key][token_type] = call_result

        return multicall_results

    def _process_market(self, market_key: HexAddress, results: dict[str, EncodedCallResult]) -> dict[str, Any]:
        """Build one market's claimable funding record from multicall results.

        :param market_key: Market address.
        :param results: The market's ``token_type`` -> call result mapping.
        :return: Claimable funding record for the market.
        """
        self._get_token_addresses(market_key)

        long_raw = self._extract_amount(results, "long")
        short_raw = self._extract_amount(results, "short")

        long_token = self._long_token_address
        short_token = self._short_token_address

        # A market whose long and short tokens are identical (e.g. the "*2"
        # markets) stores a single shared balance; do not count it twice.
        if long_token and short_token and long_token.lower() == short_token.lower():
            short_raw = 0

        long_usd = self._claimable_usd(market_key, long_token, long_raw, is_long=True)
        short_usd = self._claimable_usd(market_key, short_token, short_raw, is_long=False)

        return {
            "market": market_key,
            "long": long_usd,
            "short": short_usd,
            "total": long_usd + short_usd,
            "long_token": long_token,
            "short_token": short_token,
            "long_raw": long_raw,
            "short_raw": short_raw,
        }

    @staticmethod
    def _extract_amount(results: dict[str, EncodedCallResult], token_type: str) -> int:
        """Read one raw uint from a multicall result, defaulting to zero.

        :param results: The market's ``token_type`` -> call result mapping.
        :param token_type: Either ``"long"`` or ``"short"``.
        :return: Raw claimable amount in the token's smallest unit.
        """
        result = results.get(token_type)
        if result is not None and result.success and result.result:
            return int.from_bytes(result.result, byteorder="big")
        return 0

    def _claimable_usd(self, market_key: HexAddress, token_address: HexAddress | None, raw_amount: int, *, is_long: bool) -> float:
        """Value a raw claimable funding amount in USD via the GMX oracle.

        Follows the same convention as
        :func:`eth_defi.gmx.valuation._get_mark_price`: the oracle
        ``minPriceFull``/``maxPriceFull`` are mid-priced and divided by
        ``10 ** (PRECISION - token_decimals)`` to obtain a per-whole-token mark
        price.

        :param market_key: Market address, used to resolve token decimals.
        :param token_address: Token whose price is used for valuation.
        :param raw_amount: Raw (wei) claimable amount.
        :param is_long: Whether ``token_address`` is the market's long token.
        :return: USD value, or ``0.0`` when no oracle price is available.
        """
        if raw_amount <= 0 or not token_address:
            return 0.0

        token_data = None
        for addr, data in self.oracle_prices.items():
            if addr.lower() == token_address.lower():
                token_data = data
                break

        if not token_data or "maxPriceFull" not in token_data or "minPriceFull" not in token_data:
            logger.warning("No oracle price for token %s (market %s); USD valuation reported as zero", token_address, market_key)
            return 0.0

        try:
            decimals = self.markets.get_decimal_factor(market_key=market_key, long=is_long, short=not is_long)
            mid_price = (float(token_data["maxPriceFull"]) + float(token_data["minPriceFull"])) / 2.0
            mark_price = mid_price / (10 ** (PRECISION - decimals))
            return (raw_amount / (10**decimals)) * mark_price
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
            logger.debug("Failed to value token %s in USD: %s", token_address, e)
            return 0.0

    def generate_all_multicalls(self, markets: dict[str, Any]) -> Iterable[EncodedCall]:
        """Generate the ``DataStore.getUint`` multicalls for every market side.

        :param markets: Dictionary of available markets (keyed by market address).
        :return: Iterable of all :class:`EncodedCall` objects needed.
        """
        # DataStore.getUint() function signature: getUint(bytes32)
        get_uint_signature = keccak(text="getUint(bytes32)")[:4]

        for market_key in markets:
            self._get_token_addresses(market_key)

            for token_type, token_address in (("long", self._long_token_address), ("short", self._short_token_address)):
                if not token_address:
                    continue

                funding_key = claimable_funding_amount_key(market_key, token_address, self.account)

                yield EncodedCall.from_keccak_signature(
                    address=self.datastore_contract.address,
                    signature=get_uint_signature,
                    function=f"{token_type}_funding",
                    data=funding_key,
                    extra_data={"market_key": market_key, "token_type": token_type},
                )

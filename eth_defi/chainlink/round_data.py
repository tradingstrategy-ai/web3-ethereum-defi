"""High-level Python wrapper for Chainlink latest round data."""

import datetime
from decimal import Decimal
from dataclasses import dataclass
from functools import cached_property
from typing import Tuple

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_fromtimestamp


@dataclass
class ChainLinkLatestRoundData:
    """Human-readable presentation for Chainlink price booking.

    Wraps `IChainlinkAggregator.latestRoundData()` response.

    `See AggregatorV3Interface <https://github.com/smartcontractkit/chainlink/blob/develop/contracts/src/v0.8/interfaces/AggregatorV3Interface.sol>`__.

    Example:

    .. code-block:: python

            aggregator = chainlink_aggregator  # Point to any Chainlink aggregator contract
            round_data = fetch_chainlink_round_data(web3, aggregator.address)
            ago = native_datetime_utc_now() - round_data.update_time
            print(f"   {feed.primitive_token.symbol}, current price is {round_data.price:,.4f} USDC, Chainlink feed is {round_data.description}, updated {ago} ago")

    """

    #: See ChainlinkAggregatorV2V3Interface.sol
    aggregator: Contract

    #: Current round id
    round_id: int

    #: Price, non-decimal converted
    answer: int

    #: When processing started
    started_at: int

    #: When price was updated last time
    updated_at: int

    #: Which round gave the answer
    answered_in_round: int

    @cached_property
    def decimals(self) -> int:
        """How many decimals the aggregator has been configured for."""
        return self.aggregator.functions.decimals().call()

    @property
    def update_time(self) -> datetime.datetime:
        """Python datetime when the feed price was updated.

        - Naive timestamp

        - Always UTC
        """
        return native_datetime_utc_fromtimestamp(self.updated_at)

    @property
    def price(self) -> Decimal:
        """Human-readable price in this response."""
        return Decimal(self.answer) / Decimal(10**self.decimals)

    @cached_property
    def description(self) -> str:
        """Chainlink provided description of this feed"""
        return self.aggregator.functions.description().call()


def fetch_chainlink_round_data(web3: Web3, aggregator_address: HexAddress) -> ChainLinkLatestRoundData:
    """Fecth data from Chainlink aggregator."""
    aggregator = get_deployed_contract(
        web3,
        "ChainlinkAggregatorV2V3Interface.json",
        aggregator_address,
    )
    data = aggregator.functions.latestRoundData().call()
    return ChainLinkLatestRoundData(aggregator, *data)

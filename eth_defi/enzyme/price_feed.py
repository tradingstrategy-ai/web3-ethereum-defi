"""ChainLink price feed functions"""
from decimal import Decimal
from dataclasses import dataclass
from functools import cached_property
from typing import Iterable, Optional, Dict

from eth_typing import HexAddress, BlockNumber
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.chainlink.round_data import ChainLinkLatestRoundData
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.event_reader.conversion import decode_data, convert_uint256_bytes_to_address, convert_int256_bytes_to_int
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.reader import Web3EventReader
from eth_defi.token import fetch_erc20_details, TokenDetails
from eth_defi.utils import ZERO_ADDRESS_STR


class UnsupportedBaseAsset(Exception):
    """Cannot calculate on-chain price using Enzyme's ValueInterpreter.

    Likely the price feed was removed.
    """


@dataclass()
class EnzymePriceFeed:
    """High-level Python interface for Enzyme's ValueInterpreter price mechanism.

    - Uses `ValueInterpreter` methods to calculate on-chain price for supported assets

    .. note ::

        Enzyme price feeds are dynamic. They can be remvoed by Enzyme's risk commitee any time.

    Example:

    .. code-block:: python

        # Print out the price Enzyme sees for a token
        usdc = fetch_erc20_details(web3, POLYGON_DEPLOYMENT["usdc"])
        price = feed.calculate_current_onchain_price(usdc)
        print(f"   {feed.primitive_token.symbol}, current price is {price:,.4f} USDC")

    """

    #: The Enzyme deploymet for which this price feed is associated with
    deployment: EnzymeDeployment

    #: Token for which is price is for
    #:
    primitive: HexAddress

    #: Used contract to get the price data
    #:
    aggregator: HexAddress

    #: Do we nominate the price in USD or ETH
    #:
    rate_asset: RateAsset

    #: Decimal place divider for the price feed
    #:
    #: For ETH this is 10**18
    unit: int

    #: Solidity event where this price feed was added
    #:
    #:
    add_event: dict | None = None

    #: Solidity event where this price feed was deleted
    #:
    #:
    remove_event: dict | None = None

    def __repr__(self):
        return f"<Enzyme price feed, token:{self.primitive_token} chainlink:{self.chainlink_aggregator.address} removed:{self.remove_event is not None}>"

    def __hash__(self):
        return hash((self.web3.eth.chain_id, self.primitive))

    def __eq__(self, other):
        return self.web3.eth.chain_id == other.chain_id and self.primitive == other.primitive

    @property
    def web3(self) -> Web3:
        """The connection we use to resolve on-chain info"""
        return self.deployment.web3

    @property
    def added_block_number(self) -> BlockNumber:
        """Block number when the feed was added"""
        return self.add_event["blockNumber"]

    @property
    def removed_block_number(self) -> BlockNumber | None:
        """Block number when the feed was removed.

        :return:
            None if the feed still active
        """
        if self.remove_event:
            return self.remove_event["blockNumber"]
        return None

    @staticmethod
    def wrap(deployment: EnzymeDeployment, event: dict) -> "EnzymePriceFeed":
        """Wrap the raw Solidity event to a high-level Python interface.

        :param web3:
            Web3 connection used for further JSON-RPC API calls

        :param event:
            PrimitiveAdded Solidity event

        :return:
            Price feed instance
        """

        arguments = decode_data(event["data"])
        topics = event["topics"]

        # event PrimitiveAdded(
        #     address indexed primitive,
        #     address aggregator,
        #     RateAsset rateAsset,
        #     uint256 unit
        # );
        primitive = convert_uint256_bytes_to_address(HexBytes(topics[1]))
        aggregator = convert_uint256_bytes_to_address(arguments[0])
        rate_asset = convert_int256_bytes_to_int(arguments[1])
        unit = convert_int256_bytes_to_int(arguments[2])

        return EnzymePriceFeed(
            deployment,
            primitive,
            aggregator,
            RateAsset(rate_asset),
            unit,
            add_event=event,
        )

    @staticmethod
    def fetch_price_feed(
        deployment: EnzymeDeployment,
        token: TokenDetails,
    ) -> "EnzymePriceFeed":
        """Get a price feed for a particular token.

        :param deployment:
            Enzyme deployment.

        :param token:
            Which token we are interested in.

        :return:
            Price feed instance

        :raise UnsupportedBaseAsset:
            In the case there is no registered price feed for token
        """

        assert isinstance(token, TokenDetails)

        primitive = token.address

        value_interpreter = deployment.contracts.value_interpreter

        aggregator = value_interpreter.functions.getAggregatorForPrimitive(primitive).call()

        if aggregator == ZERO_ADDRESS_STR:
            raise UnsupportedBaseAsset(f"No Enzyme configured aggregator for: {token}")

        rate_asset = value_interpreter.functions.getRateAssetForPrimitive(primitive).call()
        unit = value_interpreter.functions.getUnitForPrimitive(primitive).call()

        return EnzymePriceFeed(
            deployment,
            primitive,
            aggregator,
            RateAsset(rate_asset),
            unit,
            add_event=None,
        )

    @cached_property
    def primitive_token(self) -> TokenDetails:
        """Access the non-indexed Solidity event arguments."""
        return fetch_erc20_details(self.web3, self.primitive, raise_on_error=False)

    @cached_property
    def chainlink_aggregator(self) -> Contract:
        """Resolve the Chainlink aggregator contract."""
        return get_deployed_contract(
            self.web3,
            "enzyme/IChainlinkAggregator.json",
            self.aggregator,
        )

    def fetch_latest_round_data(self) -> ChainLinkLatestRoundData:
        """Fetch the Chainlink round data from the underlying Chainlink price feed."""
        aggregator = self.chainlink_aggregator
        data = aggregator.functions.latestRoundData().call()
        return ChainLinkLatestRoundData(data)

    def calculate_current_onchain_price(
        self,
        quote: TokenDetails,
        amount: Decimal = Decimal(1),
    ) -> Decimal:
        """Get the primitive asset price for this price feed.

        Use Enzyme's ValueInterpreter to calculate a price in ETH or USD.

        - See `calcCanonicalAssetsTotalValue` in `ValueInterpreter`

        - See `__calcConversionAmount` in `ChainlinkPriceFeedMixin`

        :param quote:
            Which quote token we want to use for the valuation

        :param amount:
            Amount to valuate.

            If not given assume 1 token of primitive.

        :raise UnsupportedBaseAsset:
            In the case the value interpreter has the price feed removed
        """
        value_interpreter = self.deployment.contracts.value_interpreter
        raw_amount = self.primitive_token.convert_to_raw(amount)

        try:
            results = value_interpreter.functions.calcCanonicalAssetsTotalValue(
                [self.primitive_token.address],
                [raw_amount],
                quote.address,
            ).call()

            return quote.convert_to_decimals(results)
        except ContractLogicError as e:
            if "Unsupported _baseAsset" in e.args[0]:
                raise UnsupportedBaseAsset(f"Unsupported base asset: {self.primitive_token.symbol}")
            raise


def fetch_price_feeds(
    deployment: EnzymeDeployment,
    start_block: int,
    end_block: int,
    read_events: Web3EventReader,
) -> Iterable[EnzymePriceFeed]:
    """Iterate configured price feeds

    - Uses eth_getLogs ABI

    - Read both deposits and withdrawals in one go

    - Serial read

    - Slow over long block ranges

    - See `ComptrollerLib.sol`

    .. warning ::

        This function does not update status for removed price feeds. Please use
        :py:func:`fetch_updated_price_feed`.
    """

    web3 = deployment.web3

    filter = Filter.create_filter(
        deployment.contracts.value_interpreter.address,
        [deployment.contracts.value_interpreter.events.PrimitiveAdded],
    )

    for solidity_event in read_events(
        web3,
        start_block,
        end_block,
        filter=filter,
    ):
        yield EnzymePriceFeed.wrap(deployment, solidity_event)


def fetch_updated_price_feed(
    deployment: EnzymeDeployment,
    start_block: int,
    end_block: int,
    read_events: Web3EventReader,
) -> Dict[HexAddress, EnzymePriceFeed]:
    """Iterate configured price feeds.

    - Deal dynamic price feed adds and deletes

    - Uses eth_getLogs ABI

    - Read both deposits and withdrawals in one go

    - Serial read

    - Slow over long block ranges

    - See `ComptrollerLib.sol`

    :return:
        Token address -> primitive data map
    """

    web3 = deployment.web3

    filter = Filter.create_filter(
        deployment.contracts.value_interpreter.address,
        [deployment.contracts.value_interpreter.events.PrimitiveAdded, deployment.contracts.value_interpreter.events.PrimitiveRemoved],
    )

    price_feeds = {}

    for solidity_event in read_events(
        web3,
        start_block,
        end_block,
        filter=filter,
    ):
        event_name = solidity_event["event"].event_name
        primitive = convert_uint256_bytes_to_address(HexBytes(solidity_event["topics"][1]))
        match event_name:
            case "PrimitiveAdded":
                feed = EnzymePriceFeed.wrap(deployment, solidity_event)
                price_feeds[primitive] = feed
            case "PrimitiveRemoved":
                try:
                    feed = price_feeds[primitive]
                except KeyError as e:
                    raise RuntimeError(f"Got remove event for non-existing primitive {primitive} - we have {len(price_feeds)} price feeds") from e
                feed.remove_event = solidity_event

    return price_feeds

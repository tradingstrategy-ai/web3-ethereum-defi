"""Price oracle core functionality.

This core mechanism is used by outside event feeders,
like :py:mod:`eth_defi.uniswap_v2.oracle`.

"""

import datetime
import enum
import heapq
import statistics
from abc import abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Protocol, Tuple


class PriceSource(enum.Enum):
    """Different price entry sources."""

    #: Uniswap v2 pool and sync event
    uniswap_v2_like_pool_sync_event = "uniswap_v2_like_pool_sync_event"

    #: Uniswap v3 pool
    uniswap_v3_like_pool = "uniswap_v3_like_pool"

    #: Not specified
    unknown = "unknown"


@dataclass
class PriceEntry:
    """A single source entry for price calculations.

    :py:class:`PriceOracle` maintains a buffer of these to calculate
    a smoothed out price, like py:func:`time_weighted_average_price`.

    Price entry can be sourced from:

    - Manually entered price

    - Price from Uniswap v2 sync events

    - Price from some other event
    """

    #: When price entry was booked.
    #: All timestamps must be UTC, Python naive datetimes.
    timestamp: datetime.datetime

    #: When price entry was booked.
    #: This should be base token / quote token, in its human readable format,
    #: all decimals converted correctly.
    price: Decimal

    #: What was the source of this price entry
    source: PriceSource

    #: How much volume this trade carried (if available)
    #: Expressed in the quote token.
    volume: Optional[Decimal] = None

    #: Uni v2 pair contract address or similar
    pool_contract_address: Optional[str] = None

    #: Block number where this transaction happened
    block_number: Optional[int] = None

    #: Transaction where did we pick the event logs
    tx_hash: Optional[str] = None

    #: Hash of the block where this price was picked in.
    #: Can be used to remove data for blocks in unstable chain tip.
    block_hash: Optional[str] = None

    #: Chain reorganisation helper.
    #: This is set on the old event when we detect duplicate entry.
    #: We never remove items from heap, but mark them deprecated.
    #: Items are eventually cleaned up when they expire.
    first_seen_at_block_number: Optional[int] = None

    def __post_init__(self):
        """Some basic data validation."""
        assert isinstance(self.timestamp, datetime.datetime)
        assert isinstance(self.price, Decimal)
        assert isinstance(self.source, PriceSource)

        assert self.timestamp.tzinfo is None, "Timestamp only accept naive UTC datetimes"

        if self.block_number:
            assert isinstance(self.block_number, int)

    def __lt__(self, other):
        """Needed for heappush.

        https://stackoverflow.com/a/59956131/315168
        """
        assert isinstance(other, PriceEntry)
        return self.block_number < other.block_number

    def update_chain_reorg(self, new_entry: "PriceEntry"):
        """Update entry data in the case of chain reorganisation.

        TODO: We are not yet dealing with the situation if the transaction gets reorganisated
        and rejected.
        """

        self.first_seen_at_block_number = self.block_number

        # Only block number or block hash change, otherwise transactions are immutable
        self.block_number = new_entry.block_number
        self.block_hash = new_entry.block_hash


class PriceFunction(Protocol):
    """A callable for calcualte

    You can give different function for

    - Volume weighted average

    - Time weighted average
    """

    def __call__(self, events: List[PriceEntry]) -> Decimal:
        """Calculate price over multiple price samples."""


class PriceCalculationError(Exception):
    """Something wrong with price calculation."""


class NotEnoughData(PriceCalculationError):
    """The price buffer does not have enough data."""


class DataTooOld(PriceCalculationError):
    """The price buffer data does not have recent enough entries.."""


class DataPeriodTooShort(PriceCalculationError):
    """We do not have enough events for a longer period of time."""


class BasePriceOracle:
    """Base class for price oracles."""

    @abstractmethod
    def calculate_price(self, block_number: Optional[int] = None) -> Decimal:
        """Get a price for the current block.

        :param block_number:
            Hint of what is the current block.
            We do not support prices for historical blocks,
            but we may cache the result of the previous block calculation for speedups.
        """


class PriceOracle(BasePriceOracle):
    """Price oracle core.

    - Suitable for real-time price calculation for data coming over WebSockets

    - Suitable for point of time calculation using historical data

    - Sample data over multiple events

    - Rotate ring buffer of events when new data comes in.
      Uses `Python heapq <https://docs.python.org/3/library/heapq.html>`__ for this.

    Example:

    .. code-block:: python

        # Randomly chosen block range.
        # 100 blocks * 3 sec / block = ~300 seconds
        start_block = 14_000_000
        end_block = 14_000_100

        pair_details = fetch_pair_details(web3, bnb_busd_address)
        assert pair_details.token0.symbol == "WBNB"
        assert pair_details.token1.symbol == "BUSD"

        oracle = PriceOracle(
            time_weighted_average_price,
            max_age=PriceOracle.ANY_AGE,  # We are dealing with historical data
            min_duration=datetime.timedelta(minutes=1),
        )

        update_price_oracle_with_sync_events_single_thread(
            oracle,
            web3,
            bnb_busd_address,
            start_block,
            end_block
        )

        assert oracle.calculate_price() == pytest.approx(Decimal('523.8243566658033237353702655'))
    """

    #: An "infinite" place holder for max age
    ANY_AGE = datetime.timedelta(days=100 * 365)

    def __init__(
        self,
        price_function: PriceFunction,
        target_time_window: datetime.timedelta = datetime.timedelta(minutes=5),
        min_duration: datetime.timedelta = datetime.timedelta(hours=1),
        max_age: datetime.timedelta = datetime.timedelta(hours=4),
        min_entries: int = 8,
    ):
        """
        Create a new price oracle.

        The security parameters are set for a simple defaults.

        :param price_function:
            What function we use to calculate the price based on the events.
            Defaults to time-weighted average price.

        :param target_time_window:
            What is the target time window for us to calculate
            the time function. Truncation will discard older data.
            Only relevant for real-time price oracles.

        :param exchange_rate_oracle:
            If we depend on the secondary price data to calculate the price.
            E.g. converting AAVE/ETH rate to AAVE/USD using ETH/USDC pool price oracle.

        :param max_age:
            A trip wire to detect corruption in real time data feeds.
            If the most recent entry in the buffer is older than this,
            throw an exception. This usually means we have stale data in our buffer
            and some price source pool has stopped working.

        :param min_entries:
            The minimum number of entries we want to have to calculate the price reliably.

        """
        self.price_function = price_function
        self.min_duration = min_duration

        self.min_entries = min_entries
        self.max_age = max_age

        self.target_time_window = target_time_window

        # Buffer of price events using heapq.
        # The oldest datetime.datetime is the first always the first entry.
        self.buffer: List[Tuple[datetime.datetime, PriceEntry]] = []

        # In real-time mode,
        # pairs might not have seen trades for a while,
        # the last event in the buffer is valid, but old
        # but we are still actively tracking blocks.
        # Set the latest block timestamp to this entry
        # to reflect the fact that we have fresh data.
        self.last_refreshed_at: Optional[datetime.datetime] = None
        self.last_refreshed_block_number: Optional[int] = None

    def get_last_refreshed(self) -> datetime.datetime:
        """When the oracle data was refreshed last time.

        To figure out max age in real time tracking mode.
        """

        assert self.buffer

        if self.last_refreshed_at:
            return self.last_refreshed_at

        return self.get_newest().timestamp

    def update_last_refresh(self, block_number: int, timestamp: datetime.datetime):
        """Update the last seen block."""
        assert isinstance(block_number, int)
        assert isinstance(timestamp, datetime.datetime)
        self.last_refreshed_block_number = block_number
        self.last_refreshed_at = timestamp

    def check_data_quality(self, now_: Optional[datetime.datetime] = None):
        """Raises one of PriceCalculationError subclasses if our data is not good enough to calculate the oracle price.

        See :py:class:`PriceCalculationError`

        :param now_:
            Override the real-time clock for testing stale data.

        :raise PriceCalculationError:
            If we have data quality issues

        """

        if not now_:
            now_ = datetime.datetime.utcnow()

        if len(self.buffer) < self.min_entries:
            raise NotEnoughData(f"The buffer has {len(self.buffer)} entries")

        if self.get_buffer_duration() < self.min_duration:
            raise DataPeriodTooShort(f"The buffer has data for {self.get_buffer_duration()}")

        threshold = now_ - self.max_age
        last_refresh = self.get_last_refreshed()
        if last_refresh < threshold:
            raise DataTooOld(f"The data is too old (stale?).\n" f"The latest refresh is at {last_refresh}\n" f"where oracle cut off for stale data is {threshold}")

    def calculate_price(self, block_number: Optional[int] = None) -> Decimal:
        """Calculate the price based on the data in the price data buffer.

        :raise PriceCalculationError:
            If we have data quality issues.

        """
        self.check_data_quality()
        events = [tpl[1] for tpl in self.buffer]
        return self.price_function(events)

    def add_price_entry(self, evt: PriceEntry):
        """Add price entry to the ring buffer.

        .. note::

            It is not safe to call this function multiple times for the same event.

        Further reading

        - https://docs.python.org/3/library/heapq.html
        """
        assert isinstance(evt, PriceEntry)
        heapq.heappush(self.buffer, (evt.timestamp, evt))

    def add_price_entry_reorg_safe(self, evt: PriceEntry) -> bool:
        """Add price entry to the ring buffer with support for fixing chain reorganisations.

        Transactions may hop between different blocks when the chain tip reorganises,
        getting a new timestamp. In this case, we update the

        .. note::

            It is safe to call this function multiple times for the same event.

        :return:
            True if the transaction hopped to a different block
        """
        assert isinstance(evt, PriceEntry)
        assert evt.tx_hash

        existing = self.get_by_transaction_hash(evt.tx_hash)
        if existing:
            if existing.block_hash != evt.block_hash:
                existing.update_chain_reorg(evt)
        else:
            heapq.heappush(self.buffer, (evt.timestamp, evt))

    def get_by_transaction_hash(self, tx_hash: str) -> Optional[PriceEntry]:
        """Get an event by transaction hash."""
        for heap_index, entry in self.buffer:
            if entry.tx_hash == tx_hash:
                return entry
        return None

    def get_newest(self) -> Optional[PriceEntry]:
        """Return the newest price entry."""
        if self.buffer:
            largest_list = heapq.nlargest(1, self.buffer)
            return largest_list[0][1]
        return None

    def get_oldest(self) -> Optional[PriceEntry]:
        """Return the oldest price entry."""
        if self.buffer:
            return self.buffer[0][1]
        return None

    def get_buffer_duration(self) -> datetime.timedelta:
        """How long time is the time we have price events in the buffer for."""
        assert self.buffer
        return self.get_newest().timestamp - self.get_oldest().timestamp

    def feed_simple_data(self, data: Dict[datetime.datetime, Decimal], source=PriceSource.unknown):
        """Feed sample data to the price oracle from a Python dict.

        This method is mostly for testing: for actual
        implementation construct your :py:class:`PriceEntry`
        instances yourself.

        Example:

        .. code-block::

            price_data = {
                datetime.datetime(2021, 1, 3): Decimal(100),
                datetime.datetime(2021, 1, 2): Decimal(150),
                datetime.datetime(2021, 1, 1): Decimal(120),
            }

            oracle = PriceOracle(
                time_weighted_average_price,
            )

            oracle.feed_simple_data(price_data)

        """

        for key, value in data.items():
            assert isinstance(key, datetime.datetime)
            assert isinstance(value, Decimal)
            evt = PriceEntry(
                timestamp=key,
                price=value,
                source=source,
            )
            self.add_price_entry(evt)

    def truncate_buffer(self, current_timestamp: datetime.datetime) -> int:
        """Delete old data in the buffer that is no longer relevant for our price calculation.

        :return:
            Numbers of items that where discared
        """

        too_old = current_timestamp - self.target_time_window
        old_buffer_length = len(self.buffer)

        self.buffer = [entry for entry in self.buffer if entry[0] >= too_old]

        return old_buffer_length - len(self.buffer)


def time_weighted_average_price(events: List[PriceEntry]) -> Decimal:
    """Calculate TWAP price over all entries in the buffer.

    Calculates the price using :py:func:`statistics.mean`.

    Further reading:

    - https://blog.quantinsti.com/twap/

    - https://analyzingalpha.com/twap
    """

    prices = [e.price for e in events]
    return statistics.mean(prices)


class TrustedStablecoinOracle(BasePriceOracle):
    """Return a price for a token we trust we can always redeem for 1 USD."""

    STABLE_USD = Decimal(1)

    def calculate_price(self, block_number: Optional[int] = None) -> Decimal:
        return TrustedStablecoinOracle.STABLE_USD


class FixedPriceOracle(BasePriceOracle):
    """Always use the same hardcoded exchange rate.

    Most useful for unit testing.
    """

    def __init__(self, exchange_rate: Decimal):
        self.exchange_rate = exchange_rate

    def calculate_price(self, block_number: Optional[int] = None) -> Decimal:
        return self.exchange_rate

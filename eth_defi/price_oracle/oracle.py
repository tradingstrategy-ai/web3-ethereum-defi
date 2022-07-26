import datetime
import enum
import heapq
import statistics
from decimal import Decimal
from typing import Protocol, Optional, Dict, List, Tuple
from dataclasses import dataclass

from statistics import mean


class PriceSource(enum.Enum):
    uniswap_v2_like_pool_sync_event = "uniswap_v2_like_pool_sync_event"
    uniswap_v3_like_pool = "uniswap_v3_like_pool"
    unknown = "unknown"


@dataclass
class PriceEntry:
    """A single source entry for price calculations.]

    This can be:

    - Manually entered price

    - Price from Uniswap v2 sync evnet

    - Price from some other event
    """

    #: When price entry was booked.
    #: All timestamps must be UTC, Python naive datetimes.
    timestamp: datetime.datetime

    #: When price entry was booked
    price: Decimal

    #: What was the source of this trade
    source: PriceSource

    #: How much volume this trade carried.
    #: Expressed in the quote token.
    volume: Optional[Decimal] = None

    #: Uni v2 pair contract address or similar
    pair_contract_address: Optional[str] = None

    #: Block number where this transaction happened
    block_number: Optional[int] = None

    #: Transaction where did we pick the event logs
    tx_hash: Optional[str] = None

    def __post_init__(self):
        """Some basic data validation."""
        assert isinstance(self.timestamp, datetime.datetime)
        assert isinstance(self.price, Decimal)
        assert isinstance(self.source, PriceSource)

        assert self.timestamp.tzinfo is None, "Timestamp only accept naive UTC datetimes"

        if self.block_number:
            assert isinstance(self.block_number, int)


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


class PriceOracle:
    """Price oracle core.

    - Suitable for real-time price calculation for data coming over WebSockets

    - Suitable for point of time calculation using historical data

    - Sample data over multiple events

    - Rotate ring buffer of events when new data comes in.
      Uses `Python heapq <https://docs.python.org/3/library/heapq.html>`__ for this.
    """

    #: An "infinite" place holder for max age
    ANY_AGE = datetime.timedelta(days=100*365)

    def __init__(self,
                 price_function: PriceFunction,
                 min_duration: datetime.timedelta=datetime.timedelta(hours=1),
                 max_age: datetime.timedelta=datetime.timedelta(hours=4),
                 min_entries: int=8,
                 ):
        """
        Create a new price oracle.

        The security parameters are set for a simple defaults.

        :param price_function:
            What function we use to calculate the price based on the events.
            Defaults to time-weighted average price.

        :param buffer_duration:
            How much past data we need and keep to calculate the price.

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

        # Buffer of price events using heapq.
        # The oldest datetime.datetime is the first always the first entry.
        self.buffer: List[Tuple[datetime.datetime, PriceEntry]] = []

    def check_data_quality(self, now_: Optional[datetime.datetime]=None):
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
        if self.get_newest().timestamp < threshold:
            raise DataPeriodTooShort(f"The data is too old (stale?).\n"
                                     f"The latest price entry is at {self.get_newest().timestamp}\n"
                                     f"where oracle cut off for stale data is {threshold}")

    def calculate_price(self) -> Decimal:
        """Calculate the price based on the data in the price data buffer.

        :raise PriceCalculationError:
            If we have data quality issues.

        """
        self.check_data_quality()
        events = [tpl[1] for tpl in self.buffer]
        return self.price_function(events)

    def add_price_entry(self, evt: PriceEntry):
        """Add price entry to the ring buffer.

        Further reading

        - https://docs.python.org/3/library/heapq.html
        """
        heapq.heappush(self.buffer, (evt.timestamp, evt))

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

    def feed_simple_data(
            self,
            data: Dict[datetime.datetime, Decimal],
            source=PriceSource.unknown):
        """Feed sample data to the price oracle from a Python dict.

        This method is mostly for testing: for actual
        implementation construct your :py:class:`PriceEntry`
        instances yourself.
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


def time_weighted_average_price(events: List[PriceEntry]) -> Decimal:
    """Calculate TWAP price over all entries in the buffer.

    Further reading:

    - https://blog.quantinsti.com/twap/

    - https://analyzingalpha.com/twap
    """

    prices = [e.price for e in events]
    return statistics.mean(prices)




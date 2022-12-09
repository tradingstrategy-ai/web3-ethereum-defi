import datetime
import heapq
from dataclasses import dataclass
from typing import Set, Dict, Optional, Tuple, List

import pandas as pd
from attr import asdict
from eth_defi.price_oracle.oracle import PriceOracle

from .reorgmon import ReorganisationMonitor


@dataclass(slots=True)
class Trade:
    """Capture information about single trade.

    Designed for technical analysis and trading,
    prices are not intentionally unit accurate and thus
    not suitable for accounting.
    """
    pair: str
    block_number: int
    block_hash: str
    timestamp: pd.Timestamp
    tx_hash: str
    log_index: int
    value: float
    exchange_rate: float

    @staticmethod
    def get_dataframe_columns() -> dict:
        fields = dict([
            ("pair", "string"),
            ("timestamp", "datetime64[s]"),
            ("tx_hash", "string"),
            ("log_index", "uint32"),
            ("block_number", "uint64"),
            ("value", "float32"),
            ("exchange_rate", "float32"),
        ])
        return fields


class OHLCVProducer:
    """Base class for OHLCV real-time candle producers.

    In-memory latency optimised OHLCv producer for on-chain trades.

    - Keep events in RAM

    - Generate candles based on events

    - Gracefully handle chain reorganisations
    """

    def __init__(self,
                 pairs: Set[str],
                 oracles: Dict[str, PriceOracle],
                 reorg_mon: ReorganisationMonitor,
                 data_retention_time: Optional[pd.Timedelta] = None,
                 ):
        """
        Create new real-time OHLCV tracker.

        :param pairs:
            List of pool addresses

        :param oracles:
            Reference prices for converting ETH or other crypto quoted
            prices to US dollars.

            In the form of quote token address -> Price oracle maps.

        :param data_retention_time:
            Discard entries older than this to avoid
            filling the RAM.
        """

        self.pairs = pairs
        self.oracles = oracles
        self.data_retention_time = data_retention_time
        self.reorg_mon = reorg_mon

        # All event data is stored as dataframe.
        # 1. index is block_number
        # 2. index is log index within the block
        cols = Trade.get_dataframe_columns()
        self.trades_df = pd.DataFrame(columns=list(cols.keys()))
        self.trades_df = self.trades_df.astype(cols.values())

    def get_last_block(self) -> Optional[int]:
        """Get the last block number for which we have good data."""

        if len(self.trades_df) == 0:
            return Nune

        return self.trades_df.iloc[-1]["block_number"]

    def add_trades(self, trades: List[Trade]):
        """Add trade to the ring buffer with support for fixing chain reorganisations.

        Transactions may hop between different blocks when the chain tip reorganises,
        getting a new timestamp. In this case, we update the

        .. note::

            It is safe to call this function multiple times for the same event.

        :return:
            True if the transaction hopped to a different block

        :raise ChainReorganisationDetected:
            If we have detected a block reorganisation
            during importing the data

        """
        data = []

        for evt in trades:

            assert isinstance(evt, Trade)
            assert evt.tx_hash

            self.reorg_mon.check_block_reorg(evt.block_number, evt.block_hash)

            data.append(asdict(evt))

        self.trades_df.append(data)

    def truncate_reorganised_data(self, latest_good_block):
        self.trades_df.truncate(after=latest_good_block)

    def check_reorganisations_and_purge(self) -> int:
        """Check if any of block data has changed.

        :return:
            Last good safe block at the chain tip
        """
        reorg_resolution = self.reorg_mon.update_chain()

        if reorg_resolution.purge_block_number:
            self.truncate_reorganised_data(reorg_resolution.purge_block_number)

        return reorg_resolution.last_block_number

    def perform_duty_cycle(self):
        """Update the candle data

        1. Check for block reorganisations

        2. Read new data

        3. Process and index data to candles
        """
        chain_last_block = self.check_reorganisations_and_purge()




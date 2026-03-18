"""Download WBTC borrow/supply rate history from Aave v3 on Ethereum mainnet.

Uses HyperSync to stream ``ReserveDataUpdated`` events for the WBTC reserve
and saves the full history to a Parquet file with human-readable float columns.

The script is **resumable**: on subsequent runs it loads the existing Parquet
file and continues from ``max(block_number) + 1``.

Aave number formats
-------------------

All on-chain rates and indices are stored as ray-precision uint256 values
(multiply by 10^27):

- ``liquidity_rate``, ``stable_borrow_rate``, ``variable_borrow_rate`` —
  divide by RAY (10^27) to get a fraction; multiply by 100 for APR %.
  APY % = ``((1 + rate / SECONDS_PER_YEAR) ** SECONDS_PER_YEAR - 1) * 100``
  (SECONDS_PER_YEAR = 31,536,000 per Aave docs).

- ``liquidity_index``, ``variable_borrow_index`` — divide by RAY to get a
  normalised float starting at 1.0 and growing over time.  Used to compute
  accrued interest between two points in time:
  ``interest = amount * (index_end / index_start - 1)``

All conversion happens inside :py:meth:`AaveRateReader.decode_event`; raw
uint256 values are never stored.

Environment variables
---------------------

- ``JSON_RPC_ETHEREUM`` — Ethereum mainnet JSON-RPC URL (required)
- ``HYPERSYNC_API_KEY`` — HyperSync API key (optional but recommended)
- ``LOG_LEVEL`` — logging verbosity, default ``warning``

Output
------

``~/.tradingstrategy/aave/wbtc-rates-ethereum.parquet``

Columns:

- ``block_number`` (int), ``timestamp`` (datetime), ``transaction_hash`` (str),
  ``log_index`` (int), ``reserve`` (str)
- ``liquidity_rate`` — supply rate fraction (e.g. 0.05 = 5 %)
- ``stable_borrow_rate`` — stable borrow rate fraction
- ``variable_borrow_rate`` — variable borrow rate fraction
- ``liquidity_index`` — normalised supply index (starts at 1.0)
- ``variable_borrow_index`` — normalised variable borrow index (starts at 1.0)
- ``deposit_apr``, ``variable_borrow_apr``, ``stable_borrow_apr`` — APR %
- ``deposit_apy``, ``variable_borrow_apy``, ``stable_borrow_apy`` — APY %
"""

import asyncio
import datetime
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path

import hypersync
import pandas as pd
from hexbytes import HexBytes
from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.event_reader.conversion import convert_uint256_bytes_to_address
from eth_defi.hypersync.hypersync_timestamp import get_hypersync_block_height
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Aave v3 pool proxy address on Ethereum mainnet
AAVE_V3_POOL_ETHEREUM = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

#: Block when the Aave v3 pool was deployed on Ethereum mainnet
AAVE_V3_POOL_START_BLOCK = 16_291_127

#: WBTC token address on Ethereum mainnet
WBTC_ADDRESS = "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"

#: WBTC as topic1 (left-zero-padded to 32 bytes) for HyperSync log filter
WBTC_TOPIC1 = "0x0000000000000000000000002260fac5e5542a773aa44fbcfedf7c193bc2c599"

#: ReserveDataUpdated event topic0
RESERVE_DATA_UPDATED_TOPIC0 = "0x804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a"

#: Ethereum chain id
ETHEREUM_CHAIN_ID = 1

#: HyperSync stream read timeout in seconds
HYPERSYNC_READ_TIMEOUT = 90.0

#: Ray precision constant — all Aave rates and indices are multiplied by this
RAY = 10**27

#: Seconds per year used in the Aave APY formula
SECONDS_PER_YEAR = 31_536_000

#: Output Parquet file path
PARQUET_PATH = Path("~/.tradingstrategy/aave/wbtc-rates-ethereum.parquet").expanduser()


def _ray_to_float(raw: int) -> float:
    """Convert a raw ray-precision uint256 to a Python float.

    :param raw:
        On-chain uint256 value (ray-precision, i.e. scaled by 10^27).
    :return:
        Float fraction (e.g. ``50000000000000000000000000`` → ``0.05``).
    """
    return raw / RAY


def _rate_to_apr(rate_fraction: float) -> float:
    """Convert a rate fraction to APR percent.

    APR = rate_fraction × 100

    :param rate_fraction:
        Rate as a fraction (0–1 range).
    :return:
        APR as a percent value (e.g. 0.05 → 5.0).
    """
    return rate_fraction * 100


def _rate_to_apy(rate_fraction: float) -> float:
    """Convert a rate fraction to APY percent using the Aave v3 formula.

    APY = ((1 + rate / SECONDS_PER_YEAR) ^ SECONDS_PER_YEAR − 1) × 100

    Reference: https://docs.aave.com/developers/v/2.0/guides/apy-and-apr

    :param rate_fraction:
        Rate as a fraction (0–1 range).
    :return:
        APY as a percent value (e.g. 0.05 → 5.127…).
    """
    return ((1 + rate_fraction / SECONDS_PER_YEAR) ** SECONDS_PER_YEAR - 1) * 100


@dataclass(slots=True)
class AaveRateEvent:
    """A single ReserveDataUpdated event decoded from Aave v3.

    All rate and index values are already converted to human-readable floats
    (ray-precision uint256 divided by 10^27).
    """

    #: Block number the event was emitted in
    block_number: int
    #: Naive UTC block timestamp
    timestamp: datetime.datetime
    #: Transaction hash as 0x-prefixed hex string
    transaction_hash: str
    #: Log index within the block
    log_index: int
    #: Reserve token address (checksummed)
    reserve: str
    #: Supply (deposit) rate fraction — raw / RAY
    liquidity_rate: float
    #: Stable borrow rate fraction — raw / RAY
    stable_borrow_rate: float
    #: Variable borrow rate fraction — raw / RAY
    variable_borrow_rate: float
    #: Normalised liquidity (supply) index — raw / RAY, starts at 1.0
    liquidity_index: float
    #: Normalised variable borrow index — raw / RAY, starts at 1.0
    variable_borrow_index: float
    #: Supply APR percent
    deposit_apr: float
    #: Variable borrow APR percent
    variable_borrow_apr: float
    #: Stable borrow APR percent
    stable_borrow_apr: float
    #: Supply APY percent
    deposit_apy: float
    #: Variable borrow APY percent
    variable_borrow_apy: float
    #: Stable borrow APY percent
    stable_borrow_apy: float

    def as_row(self) -> dict:
        """Serialise as a plain dict suitable for ``pd.DataFrame``."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


class AaveRateReader:
    """Stream ``ReserveDataUpdated`` events for a single reserve via HyperSync.

    Follows the same structure as :py:class:`~eth_defi.aave_v3.liquidation.AaveLiquidationReader`.
    """

    def __init__(
        self,
        client: hypersync.HypersyncClient,
        web3: Web3,
        hypersync_read_timeout: float = HYPERSYNC_READ_TIMEOUT,
    ):
        assert isinstance(client, hypersync.HypersyncClient)
        assert isinstance(web3, Web3)
        self.client = client
        self.web3 = web3
        self.hypersync_read_timeout = hypersync_read_timeout

    def build_query(
        self,
        start_block: int,
        end_block: int,
        reserve_topic1: str = WBTC_TOPIC1,
    ) -> hypersync.Query:
        """Build a HyperSync query for ``ReserveDataUpdated`` events.

        Filters by:

        - Contract address: Aave v3 pool
        - topic0: ``ReserveDataUpdated`` event signature
        - topic1: reserve address (WBTC by default)

        :param start_block:
            First block to scan (inclusive).
        :param end_block:
            Last block to scan (exclusive).
        :param reserve_topic1:
            Zero-padded 32-byte reserve address as a hex string.
        :return:
            Configured :py:class:`hypersync.Query`.
        """
        return hypersync.Query(
            from_block=start_block,
            to_block=end_block,
            logs=[
                hypersync.LogSelection(
                    address=[AAVE_V3_POOL_ETHEREUM],
                    topics=[
                        [RESERVE_DATA_UPDATED_TOPIC0],
                        [reserve_topic1],
                    ],
                )
            ],
            field_selection=hypersync.FieldSelection(
                block=["number", "timestamp"],
                log=[
                    "block_number",
                    "transaction_hash",
                    "log_index",
                    "data",
                    "topic0",
                    "topic1",
                ],
            ),
        )

    def decode_event(
        self,
        log: hypersync.Log,
        block_lookup: dict,
    ) -> AaveRateEvent:
        """Decode a raw HyperSync log into a human-readable :py:class:`AaveRateEvent`.

        Converts all ray-precision uint256 fields to float fractions and
        computes APR/APY percent columns.

        :param log:
            Raw HyperSync log entry.
        :param block_lookup:
            Mapping of block number → block data (from the same HyperSync batch).
        :return:
            Decoded rate event with human-readable float values.
        """
        block = block_lookup[log.block_number]
        timestamp = native_datetime_utc_fromtimestamp(int(block.timestamp, 16))

        reserve = convert_uint256_bytes_to_address(HexBytes(log.topics[1]))

        # data: liquidity_rate, stable_borrow_rate, variable_borrow_rate,
        #        liquidity_index, variable_borrow_index  (all uint256, ray-precision)
        raw = self.web3.codec.decode(
            ["uint256", "uint256", "uint256", "uint256", "uint256"],
            bytes.fromhex(log.data[2:]),
        )

        # Convert ray-precision uint256 → float fractions
        liquidity_rate = _ray_to_float(raw[0])
        stable_borrow_rate = _ray_to_float(raw[1])
        variable_borrow_rate = _ray_to_float(raw[2])
        liquidity_index = _ray_to_float(raw[3])
        variable_borrow_index = _ray_to_float(raw[4])

        return AaveRateEvent(
            block_number=log.block_number,
            timestamp=timestamp,
            transaction_hash=log.transaction_hash,
            log_index=log.log_index,
            reserve=reserve,
            liquidity_rate=liquidity_rate,
            stable_borrow_rate=stable_borrow_rate,
            variable_borrow_rate=variable_borrow_rate,
            liquidity_index=liquidity_index,
            variable_borrow_index=variable_borrow_index,
            deposit_apr=_rate_to_apr(liquidity_rate),
            variable_borrow_apr=_rate_to_apr(variable_borrow_rate),
            stable_borrow_apr=_rate_to_apr(stable_borrow_rate),
            deposit_apy=_rate_to_apy(liquidity_rate),
            variable_borrow_apy=_rate_to_apy(variable_borrow_rate),
            stable_borrow_apy=_rate_to_apy(stable_borrow_rate),
        )

    async def fetch_rates_async(
        self,
        start_block: int,
        end_block: int,
    ) -> list[AaveRateEvent]:
        """Stream ``ReserveDataUpdated`` events via HyperSync.

        :param start_block:
            First block (inclusive).
        :param end_block:
            Last block (exclusive).
        :return:
            List of decoded :py:class:`AaveRateEvent` instances.
        """
        assert end_block >= start_block

        hypersync_chain = await self.client.get_chain_id()
        assert hypersync_chain == self.web3.eth.chain_id, (
            f"HyperSync chain {hypersync_chain} does not match Web3 chain {self.web3.eth.chain_id}"
        )

        query = self.build_query(start_block, end_block)
        logger.info("Starting HyperSync stream %d to %d", start_block, end_block)

        receiver = await self.client.stream(query, hypersync.StreamConfig())

        progress_bar = tqdm(
            total=end_block - start_block,
            desc=f"Reading Aave v3 WBTC rates: {start_block:,} – {end_block:,}",
        )

        events: list[AaveRateEvent] = []
        last_block = start_block

        while True:
            try:
                res = await asyncio.wait_for(
                    receiver.recv(),
                    timeout=self.hypersync_read_timeout,
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"HyperSync stream() read timeout after {self.hypersync_read_timeout}s"
                ) from exc

            if res is None:
                break

            current_block = res.next_block
            progress_bar.update(current_block - last_block)
            last_block = current_block

            if res.data.logs:
                block_lookup = {b.number: b for b in res.data.blocks}
                for log in res.data.logs:
                    events.append(self.decode_event(log, block_lookup))
                progress_bar.set_postfix(block=f"{current_block:,}", events=f"{len(events):,}")

        progress_bar.close()
        logger.info("Fetched %d ReserveDataUpdated events", len(events))
        return events

    def fetch_rates(
        self,
        start_block: int,
        end_block: int,
    ) -> list[AaveRateEvent]:
        """Synchronous wrapper around :py:meth:`fetch_rates_async`.

        :param start_block:
            First block (inclusive).
        :param end_block:
            Last block (exclusive).
        :return:
            List of decoded :py:class:`AaveRateEvent` instances.
        """
        return asyncio.run(self.fetch_rates_async(start_block, end_block))


def main() -> None:
    """Download WBTC Aave v3 rate history and save to Parquet."""
    log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=log_level)

    rpc_url = read_json_rpc_url(ETHEREUM_CHAIN_ID)
    web3 = create_multi_provider_web3(rpc_url)

    hypersync_url = get_hypersync_server(web3)
    hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY")
    if hypersync_api_key:
        config = hypersync.ClientConfig(url=hypersync_url, bearer_token=hypersync_api_key)
    else:
        config = hypersync.ClientConfig(url=hypersync_url)
    client = hypersync.HypersyncClient(config)

    # Load existing data for resume support
    PARQUET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PARQUET_PATH.exists():
        existing_df = pd.read_parquet(PARQUET_PATH)
        start_block = int(existing_df["block_number"].max()) + 1
        logger.info("Resuming from block %d (%d existing rows)", start_block, len(existing_df))
    else:
        existing_df = pd.DataFrame()
        start_block = AAVE_V3_POOL_START_BLOCK
        logger.info("Starting fresh from block %d", start_block)

    end_block = get_hypersync_block_height(client)
    logger.info("Chain tip: block %d", end_block)

    if start_block >= end_block:
        print("Already up to date.")
        return

    reader = AaveRateReader(client=client, web3=web3)
    new_events = reader.fetch_rates(start_block=start_block, end_block=end_block)

    new_df = pd.DataFrame([e.as_row() for e in new_events]) if new_events else pd.DataFrame()
    merged_df = (
        pd.concat([existing_df, new_df], ignore_index=True)
        .drop_duplicates(subset=["block_number", "log_index"])
        .sort_values("block_number")
        .reset_index(drop=True)
    )

    merged_df.to_parquet(PARQUET_PATH, index=False)
    file_size_mib = PARQUET_PATH.stat().st_size / 1024**2
    print(f"Saved {len(merged_df):,} rows to {PARQUET_PATH} ({file_size_mib:.2f} MiB)")

    if not merged_df.empty:
        summary = (
            merged_df
            .tail(10)[["block_number", "timestamp", "deposit_apr", "variable_borrow_apr", "stable_borrow_apr"]]
            .copy()
        )
        summary["timestamp"] = pd.to_datetime(summary["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
        for col in ["deposit_apr", "variable_borrow_apr", "stable_borrow_apr"]:
            summary[col] = summary[col].map("{:.4f}%".format)
        print()
        print("Most recent 10 WBTC rate events on Aave v3 Ethereum:")
        print(tabulate(summary.values.tolist(), headers=list(summary.columns), tablefmt="simple"))
        print()
        ts = pd.to_datetime(merged_df["timestamp"])
        print(f"Total rows : {len(merged_df):,}")
        print(f"Date range : {ts.min().strftime('%Y-%m-%d')} — {ts.max().strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    main()

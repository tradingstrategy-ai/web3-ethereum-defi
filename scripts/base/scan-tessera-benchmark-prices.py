"""Collect onchain benchmark prices for the Tessera propAMM execution-quality analysis.

The Tessera dataset (see ``scan-tessera-propamm.py``) can only show that the
venue fills worse than *its own* end-of-previous-block quote. To say whether
retail would have been better off elsewhere -- the claim aggregators route
on -- every trade needs a fair reference price at the same moment.

This script streams ``Swap`` events from the deepest concentrated-liquidity
pool per traded pair on Base and stores the pool's marginal price per block.
Uniswap V3 and Aerodrome Slipstream (CL) pools share the same event::

    Swap(address indexed sender, address indexed recipient, int256 amount0,
         int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick)

Pool selection is automatic: for the most-traded Tessera pairs, candidate
pools are resolved from the Uniswap V3 factory (all fee tiers) and the
Aerodrome CL factory (all tick spacings), then the pool with the most swaps in
a recent window wins. Override with ``BENCHMARK_POOLS``.

Schema added to the Tessera DuckDB file (no ``PRIMARY KEY``/``UNIQUE``, see
``CLAUDE.md`` on DuckDB ART indexes):

- ``benchmark_pools`` — chosen pool per pair: protocol, token0/token1,
  decimals, fee, tick spacing.
- ``benchmark_swaps`` — every raw swap: amounts, ``sqrtPriceX96``, decimal
  adjusted price (token1 per token0), liquidity, tick, position in block.
- ``benchmark_block_prices`` — end-of-block price per pool per block that had
  a swap (last swap by log index), rebuilt for the scanned range each run.
- ``trade_benchmarks`` — one row per Tessera trade in the scanned range with
  the reference price at the end of the previous block (same baseline moment
  as the Tessera quote in ``trade_analysis``). Materialised with an ``ASOF``
  join at scan time because DuckDB 1.5 hangs on ``LIMIT`` over an ``ASOF``
  join inside a view.
- ``trade_vs_benchmark`` view — ``trade_analysis`` joined to ``trade_benchmarks``, with:

  - ``fill_vs_benchmark_bps`` — how much worse the user's fill was than the
    reference pool's marginal price; positive = user got less.
  - ``quote_vs_benchmark_bps`` — how much better Tessera's *quote* was than
    the reference; this is the "price improvement" aggregators saw.
  - ``benchmark_age_blocks`` — staleness of the reference price.

The reference is the pool's marginal (mid) price with no swap fee and no
price impact, so it flatters the pool slightly: an actual fill there would
pay ``benchmark_pools.fee_bps`` plus impact. Add those in the analysis when
comparing against a hypothetical Uniswap fill rather than against fair value.

Usage::

    # Default: benchmark the enriched window (block 50,000,000 to tip)
    source .local-test.env && poetry run python scripts/base/scan-tessera-benchmark-prices.py

    # Full Tessera history
    source .local-test.env && START_BLOCK=37518780 poetry run python scripts/base/scan-tessera-benchmark-prices.py

Environment variables:

- ``JSON_RPC_BASE``, ``HYPERSYNC_API_KEY`` — as for the main scanner.
- ``TESSERA_DUCKDB_PATH`` — default ``~/.tradingstrategy/tessera/tessera-base.duckdb``;
  must already contain ``trades`` and ``tokens`` (pair discovery reads them).
- ``START_BLOCK`` / ``END_BLOCK`` — explicit range. ``START_BLOCK`` defaults
  to the saved ``benchmark_last_scanned_block`` + 1, else
  ``BENCHMARK_START_BLOCK`` (default ``50000000``).
- ``CHUNK_BLOCKS`` — default ``30000``; keep it small enough that a chunk
  needs fewer than 30 Hypersync pages (about 7,000 blocks per page on swap-dense ranges).
- ``BENCHMARK_MAX_PAIRS`` — how many top Tessera pairs to benchmark, default ``8``.
- ``BENCHMARK_POOLS`` — comma separated pool addresses to use instead of discovery.
- ``POOL_DISCOVERY_LOOKBACK_BLOCKS`` — swap-count window for choosing pools, default ``20000``.
- ``HYPERSYNC_BATCH_SIZE`` (default ``50000`` blocks per page) and
  ``HYPERSYNC_RESPONSE_BYTES_CEILING`` (default 200 MB) — stream paging; the
  free Hypersync tier allows 30 requests/minute, so pages must be large.
- ``HYPERSYNC_PAGES_PER_MINUTE`` (default ``25``) — sleep between chunks so the
  amortised page rate stays under the server budget.
- ``REBUILD_START_BLOCK`` — rebuild ``benchmark_block_prices`` and
  ``trade_benchmarks`` from this block instead of the scan start; use after a
  run that resumed part-way so the derived tables cover the whole range.
- ``CHECKPOINT_EVERY_CHUNKS`` (default ``5``), ``MIN_FREE_DISK_GB`` (default ``10``),
  ``HYPERSYNC_RECV_TIMEOUT``, ``HYPERSYNC_MAX_ATTEMPTS``, ``HYPERSYNC_MAX_BACKOFF_SECONDS``,
  ``HYPERSYNC_RPM`` — as for the main scanner.
- ``LOG_LEVEL`` — default ``info``.
"""

import asyncio
import datetime
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import eth_abi
import pandas as pd
from eth_typing import HexAddress
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.exceptions import Web3Exception

import hypersync
from hypersync import BlockField, LogField

from eth_defi.hypersync.hypersync_timestamp import is_hypersync_retryable_runtime_error
from eth_defi.hypersync.session import ThrottledHypersyncClient
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.compat import native_datetime_utc_now
from eth_defi.utils import setup_console_logging
from eth_defi.vault.flow_events import decode_hypersync_int

logger = logging.getLogger(__name__)

CHAIN_ID = 8453

#: Uniswap V3 factory on Base
UNISWAP_V3_FACTORY: HexAddress = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"

#: Uniswap V3 fee tiers to probe
UNISWAP_V3_FEES = (100, 500, 3000, 10000)

#: Aerodrome Slipstream (concentrated liquidity) factory on Base
AERODROME_CL_FACTORY: HexAddress = "0x5e7BB104d84c7CB9B682AaC2F3d509f5F406809A"

#: Aerodrome CL tick spacings to probe
AERODROME_TICK_SPACINGS = (1, 50, 100, 200, 2000)

#: Swap(address,address,int256,int256,uint160,uint128,int24), shared by Uniswap V3 and Aerodrome CL
SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

SELECTOR_GET_POOL_FEE = Web3.keccak(text="getPool(address,address,uint24)")[:4]
SELECTOR_GET_POOL_TICK_SPACING = Web3.keccak(text="getPool(address,address,int24)")[:4]
SELECTOR_TOKEN0 = Web3.keccak(text="token0()")[:4]
SELECTOR_TOKEN1 = Web3.keccak(text="token1()")[:4]
SELECTOR_FEE = Web3.keccak(text="fee()")[:4]
SELECTOR_TICK_SPACING = Web3.keccak(text="tickSpacing()")[:4]

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

#: DuckDB HUGEINT upper bound
HUGEINT_MAX = 2**127 - 1

HYPERSYNC_MAX_ATTEMPTS = int(os.environ.get("HYPERSYNC_MAX_ATTEMPTS", "10"))
HYPERSYNC_MAX_BACKOFF_SECONDS = int(os.environ.get("HYPERSYNC_MAX_BACKOFF_SECONDS", "120"))

#: Stream paging tuning. The free Hypersync tier allows 30 requests/minute and
#: the stream paginates by response size, so a swap-dense chunk can need more
#: than 30 pages with server defaults. Larger pages mean fewer requests.
HYPERSYNC_BATCH_SIZE = int(os.environ.get("HYPERSYNC_BATCH_SIZE", "50000"))
HYPERSYNC_RESPONSE_BYTES_CEILING = int(os.environ.get("HYPERSYNC_RESPONSE_BYTES_CEILING", str(200 * 1024 * 1024)))

#: Amortised page fetches per minute. The server caps a page at roughly 7,000
#: blocks for swap-dense queries regardless of the byte ceiling, and a retry
#: re-fetches the whole chunk, so chunks must need fewer than 30 pages and
#: consecutive chunks must be paced below the budget.
HYPERSYNC_PAGES_PER_MINUTE = int(os.environ.get("HYPERSYNC_PAGES_PER_MINUTE", "25"))


@dataclass(slots=True)
class BenchmarkConfig:
    """Runtime configuration parsed from environment variables."""

    duckdb_path: Path
    start_block: int
    end_block: int
    chunk_blocks: int
    max_pairs: int
    pool_override: list[str]
    discovery_lookback_blocks: int
    checkpoint_every_chunks: int
    min_free_disk_gb: float
    recv_timeout: float


@dataclass(slots=True)
class BenchmarkPool:
    """A concentrated-liquidity pool chosen as the reference for one pair."""

    address: HexAddress
    protocol: str
    token0: HexAddress
    token1: HexAddress
    decimals0: int
    decimals1: int
    #: Swap fee in basis points, if the pool exposes ``fee()``
    fee_bps: float | None
    tick_spacing: int | None
    #: Swaps observed in the discovery window
    recent_swaps: int


def hugeint_str(value: int | None) -> str | None:
    """Convert an int256 into a HUGEINT-safe string, clamping overflow to NULL with a warning."""
    if value is None:
        return None
    if abs(value) > HUGEINT_MAX:
        logger.warning("Value %d exceeds HUGEINT range, storing NULL", value)
        return None
    return str(value)


def hex_int(value: str | int | None) -> int | None:
    """Decode a Hypersync hex or int field, tolerating missing values."""
    if value is None:
        return None
    return decode_hypersync_int(value)


def to_bytes(value: str | bytes | None) -> bytes:
    """Normalise Hypersync hex data to bytes."""
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return bytes.fromhex(value[2:] if value.startswith("0x") else value)


def sqrt_price_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    """Convert Uniswap V3 ``sqrtPriceX96`` to a decimal adjusted token1-per-token0 price."""
    ratio = (sqrt_price_x96 / 2**96) ** 2
    return ratio * 10 ** (decimals0 - decimals1)


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create benchmark tables and the trade-vs-benchmark view if missing."""
    con.execute("SET wal_autocheckpoint = '1TB'")
    con.execute("""
        CREATE TABLE IF NOT EXISTS scan_state (
            key VARCHAR,
            value BIGINT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_pools (
            pool VARCHAR,
            protocol VARCHAR,
            token0 VARCHAR,
            token1 VARCHAR,
            symbol0 VARCHAR,
            symbol1 VARCHAR,
            decimals0 INTEGER,
            decimals1 INTEGER,
            fee_bps DOUBLE,
            tick_spacing INTEGER,
            recent_swaps INTEGER,
            chosen_at TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_swaps (
            block_number BIGINT,
            timestamp TIMESTAMP,
            pool VARCHAR,
            tx_hash VARCHAR,
            tx_index INTEGER,
            log_index INTEGER,
            sender VARCHAR,
            recipient VARCHAR,
            amount0 HUGEINT,
            amount1 HUGEINT,
            sqrt_price_x96 VARCHAR,
            price DOUBLE,
            liquidity DOUBLE,
            tick INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_block_prices (
            pool VARCHAR,
            block_number BIGINT,
            timestamp TIMESTAMP,
            price DOUBLE,
            tick INTEGER,
            liquidity DOUBLE,
            swap_count INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_benchmarks (
            tx_hash VARCHAR,
            log_index INTEGER,
            block_number BIGINT,
            pool VARCHAR,
            benchmark_block BIGINT,
            benchmark_price DOUBLE,
            benchmark_swap_count INTEGER
        )
    """)
    con.execute("""
        CREATE OR REPLACE VIEW trade_vs_benchmark AS
        WITH m AS (
            SELECT
                a.venue, a.block_number, a.timestamp, a.tx_hash, a.log_index,
                a.token_in, a.token_out, a.symbol_in, a.symbol_out,
                a.amount_in_decimal, a.amount_out_decimal, a.fill_price,
                a.aggregator, a.frontend, a.wallet_kind, a.router, a.is_self_call,
                a.rel_gas_position, a.prev_quoted_out, a.quote_to_fill_bps,
                a.user_slippage_bps, a.user_min_amount_out,
                p.pool, p.protocol, p.fee_bps AS benchmark_fee_bps, p.token0, p.token1,
                a.token_in = p.token0 AS sells_token0,
                a.block_number - 1 AS quote_block,
                tb.benchmark_block, tb.benchmark_price, tb.benchmark_swap_count,
                -- Effective fill and quote prices in token1-per-token0 terms, regardless of trade direction
                CASE WHEN a.token_in = p.token0 THEN a.amount_out_decimal / NULLIF(a.amount_in_decimal, 0)
                     ELSE a.amount_in_decimal / NULLIF(a.amount_out_decimal, 0) END AS fill_price_t1_per_t0,
                CASE WHEN a.token_in = p.token0 THEN (a.prev_quoted_out / POWER(10, tok.decimals)) / NULLIF(a.amount_in_decimal, 0)
                     ELSE a.amount_in_decimal / NULLIF(a.prev_quoted_out / POWER(10, tok.decimals), 0) END AS quote_price_t1_per_t0
            FROM trade_analysis a
            JOIN tokens tok ON tok.address = a.token_out
            JOIN trade_benchmarks tb ON tb.tx_hash = a.tx_hash AND tb.log_index = a.log_index
            JOIN benchmark_pools p ON p.pool = tb.pool
        )
        SELECT
            *,
            quote_block - benchmark_block AS benchmark_age_blocks,
            -- Positive = the user received less than the reference pool's marginal price implied
            CASE WHEN sells_token0 THEN (benchmark_price - fill_price_t1_per_t0) * 10000.0 / NULLIF(benchmark_price, 0)
                 ELSE (fill_price_t1_per_t0 - benchmark_price) * 10000.0 / NULLIF(benchmark_price, 0) END AS fill_vs_benchmark_bps,
            -- Positive = Tessera's end-of-previous-block quote was better than the reference (the routing "price improvement")
            CASE WHEN sells_token0 THEN (quote_price_t1_per_t0 - benchmark_price) * 10000.0 / NULLIF(benchmark_price, 0)
                 ELSE (benchmark_price - quote_price_t1_per_t0) * 10000.0 / NULLIF(benchmark_price, 0) END AS quote_vs_benchmark_bps
        FROM m
    """)


def read_scan_state(con: duckdb.DuckDBPyConnection, key: str) -> int | None:
    """Read a scan progress marker."""
    row = con.execute("SELECT value FROM scan_state WHERE key = ?", [key]).fetchone()
    return int(row[0]) if row else None


def write_scan_state(con: duckdb.DuckDBPyConnection, key: str, value: int) -> None:
    """Upsert a scan progress marker."""
    con.execute("DELETE FROM scan_state WHERE key = ?", [key])
    con.execute("INSERT INTO scan_state VALUES (?, ?)", [key, value])


def insert_frame(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame, columns: list[str]) -> None:
    """Insert a DataFrame with explicit column order so string amounts cast to HUGEINT."""
    if df.empty:
        return
    con.register("_incoming", df[columns])
    col_list = ", ".join(columns)
    con.execute(f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM _incoming")
    con.unregister("_incoming")


async def _collect_stream(client: ThrottledHypersyncClient, query: hypersync.Query, progress: tqdm | None, recv_timeout: float) -> tuple[list, list, int]:
    """Drain a Hypersync stream into (blocks, logs, pages), advancing the progress bar per response."""
    stream_config = client.create_stream_config(
        batch_size=HYPERSYNC_BATCH_SIZE,
        max_batch_size=HYPERSYNC_BATCH_SIZE,
        response_bytes_ceiling=HYPERSYNC_RESPONSE_BYTES_CEILING,
    )
    receiver = await client.stream(query, stream_config)
    blocks, logs = [], []
    pages = 0
    last_block = query.from_block
    while True:
        res = await asyncio.wait_for(receiver.recv(), timeout=recv_timeout)
        if res is None:
            break
        pages += 1
        blocks += res.data.blocks or []
        logs += res.data.logs or []
        if progress is not None and res.next_block is not None:
            progress.update(res.next_block - last_block)
            progress.set_postfix({"swaps": f"{len(logs):,}"})
            last_block = res.next_block
    if progress is not None and query.to_block > last_block:
        progress.update(query.to_block - last_block)
    return blocks, logs, pages


def collect_stream(client: ThrottledHypersyncClient, query: hypersync.Query, context: str, recv_timeout: float) -> tuple[list, list, int]:
    """Run a Hypersync query synchronously with a progress bar and retries for transient failures.

    :return:
        Tuple (blocks, logs, pages fetched).
    """
    for attempt in range(1, HYPERSYNC_MAX_ATTEMPTS + 1):
        with tqdm(total=query.to_block - query.from_block, desc=f"Hypersync {context}", unit="block", leave=False) as progress:
            try:
                return asyncio.run(_collect_stream(client, query, progress, recv_timeout))
            except (RuntimeError, asyncio.TimeoutError) as e:
                msg = str(e)
                retryable = isinstance(e, asyncio.TimeoutError) or is_hypersync_retryable_runtime_error(e) or "inner receiver" in msg or "timed out" in msg
                if not retryable:
                    raise
                wait = min(HYPERSYNC_MAX_BACKOFF_SECONDS, 5 * 2 ** (attempt - 1))
                if attempt == HYPERSYNC_MAX_ATTEMPTS:
                    logger.error("Hypersync %s failed after %d attempts: %s", context, attempt, msg)
                    raise
                logger.warning("Hypersync %s attempt %d failed (%s), retrying in %d s", context, attempt, " | ".join(line.strip() for line in msg.splitlines() if line.strip())[:300] if msg else type(e).__name__, wait)
                time.sleep(wait)
    raise AssertionError("unreachable")


def eth_call_address(web3: Web3, to: str, data: bytes) -> HexAddress | None:
    """Call a view returning an address; ``None`` for the zero address or a revert."""
    try:
        out = web3.eth.call({"to": Web3.to_checksum_address(to), "data": data})
    except (ValueError, Web3Exception):
        return None
    if len(out) < 32:
        return None
    address = Web3.to_checksum_address("0x" + out.hex()[-40:])
    return None if address.lower() == ZERO_ADDRESS else address


def eth_call_int(web3: Web3, to: str, data: bytes, signed: bool = False) -> int | None:
    """Call a view returning a single integer; ``None`` on revert."""
    try:
        out = web3.eth.call({"to": Web3.to_checksum_address(to), "data": data})
    except (ValueError, Web3Exception):
        return None
    if len(out) < 32:
        return None
    return int.from_bytes(out[:32], "big", signed=signed)


def fetch_top_pairs(con: duckdb.DuckDBPyConnection, max_pairs: int) -> list[tuple[str, str, int]]:
    """Most traded unordered Tessera token pairs, as (token_a, token_b, trade_count) with token_a < token_b."""
    rows = con.execute(
        """
        SELECT least(lower(token_in), lower(token_out)) a, greatest(lower(token_in), lower(token_out)) b, count(*) n
        FROM trades WHERE venue = 'tessera'
        GROUP BY 1, 2 ORDER BY n DESC LIMIT ?
        """,
        [max_pairs],
    ).fetchall()
    return [(Web3.to_checksum_address(a), Web3.to_checksum_address(b), int(n)) for a, b, n in rows]


def discover_candidate_pools(web3: Web3, token_a: str, token_b: str) -> list[tuple[str, str, int | None]]:
    """Resolve every Uniswap V3 fee tier and Aerodrome CL tick spacing pool for a pair.

    :return:
        List of (pool address, protocol, fee or tick spacing parameter)
    """
    candidates = []
    for fee in UNISWAP_V3_FEES:
        pool = eth_call_address(web3, UNISWAP_V3_FACTORY, SELECTOR_GET_POOL_FEE + eth_abi.encode(["address", "address", "uint24"], [token_a, token_b, fee]))
        if pool:
            candidates.append((pool, "uniswap_v3", fee))
    for spacing in AERODROME_TICK_SPACINGS:
        pool = eth_call_address(web3, AERODROME_CL_FACTORY, SELECTOR_GET_POOL_TICK_SPACING + eth_abi.encode(["address", "address", "int24"], [token_a, token_b, spacing]))
        if pool:
            candidates.append((pool, "aerodrome_cl", spacing))
    return candidates


def count_recent_swaps(client: ThrottledHypersyncClient, pools: list[str], start_block: int, end_block: int, recv_timeout: float) -> dict[str, int]:
    """Count Swap logs per pool over a block window with one Hypersync query."""
    if not pools:
        return {}
    query = hypersync.Query(
        from_block=start_block,
        to_block=end_block + 1,
        logs=[hypersync.LogSelection(address=[p.lower() for p in pools], topics=[[SWAP_TOPIC]])],
        join_mode=hypersync.JoinMode.JOIN_NOTHING,
        field_selection=hypersync.FieldSelection(log=[LogField.ADDRESS]),
    )
    _, logs, _ = collect_stream(client, query, f"pool discovery {start_block:,}-{end_block:,}", recv_timeout)
    counts: dict[str, int] = {}
    for log in logs:
        key = log.address.lower()
        counts[key] = counts.get(key, 0) + 1
    return counts


def describe_pool(web3: Web3, con: duckdb.DuckDBPyConnection, pool: str, protocol: str, recent_swaps: int) -> BenchmarkPool | None:
    """Read token0/token1, decimals, fee and tick spacing for a pool."""
    token0 = eth_call_address(web3, pool, SELECTOR_TOKEN0)
    token1 = eth_call_address(web3, pool, SELECTOR_TOKEN1)
    if not token0 or not token1:
        logger.warning("Pool %s does not expose token0/token1, skipping", pool)
        return None
    decimals = dict(con.execute("SELECT address, decimals FROM tokens WHERE address IN (?, ?)", [token0, token1]).fetchall())
    if token0 not in decimals or token1 not in decimals:
        logger.warning("Pool %s tokens missing from tokens table, skipping", pool)
        return None
    fee_raw = eth_call_int(web3, pool, SELECTOR_FEE)
    tick_spacing = eth_call_int(web3, pool, SELECTOR_TICK_SPACING, signed=True)
    return BenchmarkPool(
        address=Web3.to_checksum_address(pool),
        protocol=protocol,
        token0=token0,
        token1=token1,
        decimals0=int(decimals[token0]),
        decimals1=int(decimals[token1]),
        # Both Uniswap V3 and Aerodrome CL express fee() in hundredths of a bip (1e-6)
        fee_bps=fee_raw / 100 if fee_raw is not None else None,
        tick_spacing=tick_spacing,
        recent_swaps=recent_swaps,
    )


def choose_pools(con: duckdb.DuckDBPyConnection, web3: Web3, client: ThrottledHypersyncClient, config: BenchmarkConfig) -> list[BenchmarkPool]:
    """Pick one reference pool per top Tessera pair, or use the override list."""
    if config.pool_override:
        chosen = [describe_pool(web3, con, pool, "override", 0) for pool in config.pool_override]
        return [p for p in chosen if p is not None]

    pairs = fetch_top_pairs(con, config.max_pairs)
    logger.info("Discovering reference pools for %d pairs", len(pairs))
    candidates: dict[str, tuple[str, str, str, int | None]] = {}
    for token_a, token_b, n in pairs:
        for pool, protocol, param in discover_candidate_pools(web3, token_a, token_b):
            candidates[pool.lower()] = (f"{token_a}/{token_b}", pool, protocol, param)
    logger.info("Found %d candidate pools, counting recent swaps", len(candidates))

    counts = count_recent_swaps(client, list(candidates), config.end_block - config.discovery_lookback_blocks, config.end_block, config.recv_timeout)

    best: dict[str, tuple[int, str, str]] = {}
    for key, (pair, pool, protocol, _param) in candidates.items():
        swaps = counts.get(key, 0)
        if swaps > best.get(pair, (-1, "", ""))[0]:
            best[pair] = (swaps, pool, protocol)

    chosen = []
    for pair, (swaps, pool, protocol) in best.items():
        if swaps == 0:
            logger.warning("No recent swaps in any candidate pool for %s, skipping", pair)
            continue
        described = describe_pool(web3, con, pool, protocol, swaps)
        if described:
            chosen.append(described)
            logger.info("Pair %s -> %s %s (%d swaps in last %d blocks, fee %s bps)", pair, protocol, pool, swaps, config.discovery_lookback_blocks, described.fee_bps)
    return chosen


def store_pools(con: duckdb.DuckDBPyConnection, pools: list[BenchmarkPool]) -> None:
    """Replace the benchmark_pools table with the current selection."""
    symbols = dict(con.execute("SELECT address, symbol FROM tokens").fetchall())
    rows = [
        {
            "pool": p.address,
            "protocol": p.protocol,
            "token0": p.token0,
            "token1": p.token1,
            "symbol0": symbols.get(p.token0),
            "symbol1": symbols.get(p.token1),
            "decimals0": p.decimals0,
            "decimals1": p.decimals1,
            "fee_bps": p.fee_bps,
            "tick_spacing": p.tick_spacing,
            "recent_swaps": p.recent_swaps,
            "chosen_at": native_datetime_utc_now(),
        }
        for p in pools
    ]
    con.begin()
    con.execute("DELETE FROM benchmark_pools")
    insert_frame(con, "benchmark_pools", pd.DataFrame(rows), list(rows[0].keys()) if rows else [])
    con.commit()


def build_swap_query(pools: list[str], start_block: int, end_block: int) -> hypersync.Query:
    """Swap logs for the chosen pools with joined block timestamps."""
    return hypersync.Query(
        from_block=start_block,
        to_block=end_block + 1,
        logs=[hypersync.LogSelection(address=[p.lower() for p in pools], topics=[[SWAP_TOPIC]])],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[LogField.BLOCK_NUMBER, LogField.LOG_INDEX, LogField.TRANSACTION_HASH, LogField.TRANSACTION_INDEX, LogField.ADDRESS, LogField.TOPIC1, LogField.TOPIC2, LogField.DATA],
        ),
    )


def decode_swap_log(log, block, pool: BenchmarkPool) -> dict:
    """Decode one Swap log into a benchmark_swaps row."""
    amount0, amount1, sqrt_price_x96, liquidity, tick = eth_abi.decode(["int256", "int256", "uint160", "uint128", "int24"], to_bytes(log.data))
    return {
        "block_number": hex_int(log.block_number),
        "timestamp": datetime.datetime.fromtimestamp(hex_int(block.timestamp), tz=datetime.timezone.utc).replace(tzinfo=None),
        "pool": pool.address,
        "tx_hash": log.transaction_hash,
        "tx_index": hex_int(log.transaction_index),
        "log_index": hex_int(log.log_index),
        "sender": Web3.to_checksum_address("0x" + log.topics[1][-40:]) if len(log.topics) > 1 and log.topics[1] else None,
        "recipient": Web3.to_checksum_address("0x" + log.topics[2][-40:]) if len(log.topics) > 2 and log.topics[2] else None,
        "amount0": hugeint_str(amount0),
        "amount1": hugeint_str(amount1),
        "sqrt_price_x96": str(sqrt_price_x96),
        "price": sqrt_price_to_price(sqrt_price_x96, pool.decimals0, pool.decimals1),
        "liquidity": float(liquidity),
        "tick": int(tick),
    }


SWAP_COLUMNS = ["block_number", "timestamp", "pool", "tx_hash", "tx_index", "log_index", "sender", "recipient", "amount0", "amount1", "sqrt_price_x96", "price", "liquidity", "tick"]


def scan_swap_chunk(con: duckdb.DuckDBPyConnection, client: ThrottledHypersyncClient, pools: dict[str, BenchmarkPool], start_block: int, end_block: int, recv_timeout: float) -> tuple[int, int]:
    """Scan one block range of Swap logs and commit it, replacing any existing rows in range.

    :return:
        Tuple (swap rows written, Hypersync pages fetched).
    """
    blocks, logs, pages = collect_stream(client, build_swap_query(list(pools), start_block, end_block), f"swaps {start_block:,}-{end_block:,}", recv_timeout)
    block_by_number = {hex_int(b.number): b for b in blocks}
    rows = []
    for log in logs:
        pool = pools.get(log.address.lower())
        block = block_by_number.get(hex_int(log.block_number))
        if pool is None or block is None:
            logger.warning("Missing pool or block for swap %s", log.transaction_hash)
            continue
        rows.append(decode_swap_log(log, block, pool))

    con.begin()
    con.execute("DELETE FROM benchmark_swaps WHERE block_number BETWEEN ? AND ?", [start_block, end_block])
    insert_frame(con, "benchmark_swaps", pd.DataFrame(rows), SWAP_COLUMNS)
    write_scan_state(con, "benchmark_last_scanned_block", end_block)
    con.commit()
    return len(rows), pages


def pace_pages(pages: int, started: float) -> None:
    """Sleep after a chunk so the amortised page rate stays under the server budget."""
    if HYPERSYNC_PAGES_PER_MINUTE <= 0:
        return
    minimum_seconds = pages * 60.0 / HYPERSYNC_PAGES_PER_MINUTE
    remaining = minimum_seconds - (time.time() - started)
    if remaining > 0:
        logger.info("Pacing: %d pages fetched, sleeping %.0f s to stay under %d pages/min", pages, remaining, HYPERSYNC_PAGES_PER_MINUTE)
        time.sleep(remaining)


def rebuild_block_prices(con: duckdb.DuckDBPyConnection, start_block: int, end_block: int) -> tuple[int, int]:
    """Materialise end-of-block prices and per-trade benchmarks for the scanned range.

    The per-trade ASOF join runs here, once, instead of inside the view: DuckDB
    1.5 hangs on ``LIMIT`` over an ``ASOF`` join in a view, which every
    notebook query would hit.

    :return:
        Tuple (block price rows, trade benchmark rows) written for the range.
    """
    con.begin()
    con.execute("DELETE FROM benchmark_block_prices WHERE block_number BETWEEN ? AND ?", [start_block, end_block])
    con.execute(
        """
        INSERT INTO benchmark_block_prices (pool, block_number, timestamp, price, tick, liquidity, swap_count)
        SELECT pool, block_number, any_value(timestamp),
               arg_max(price, log_index), arg_max(tick, log_index), arg_max(liquidity, log_index), count(*)
        FROM benchmark_swaps
        WHERE block_number BETWEEN ? AND ?
        GROUP BY pool, block_number
        """,
        [start_block, end_block],
    )
    con.execute("DELETE FROM trade_benchmarks WHERE block_number BETWEEN ? AND ?", [start_block, end_block])
    con.execute(
        """
        INSERT INTO trade_benchmarks (tx_hash, log_index, block_number, pool, benchmark_block, benchmark_price, benchmark_swap_count)
        SELECT t.tx_hash, t.log_index, t.block_number, p.pool, b.block_number, b.price, b.swap_count
        FROM (
            SELECT tx_hash, log_index, block_number, token_in, token_out
            FROM trades
            WHERE venue = 'tessera' AND block_number BETWEEN ? AND ?
        ) t
        JOIN benchmark_pools p
          ON (p.token0 = t.token_in AND p.token1 = t.token_out)
          OR (p.token0 = t.token_out AND p.token1 = t.token_in)
        ASOF LEFT JOIN benchmark_block_prices b
          ON b.pool = p.pool AND b.block_number <= t.block_number - 1
        """,
        [start_block, end_block],
    )
    con.commit()
    prices = con.execute("SELECT count(*) FROM benchmark_block_prices WHERE block_number BETWEEN ? AND ?", [start_block, end_block]).fetchone()[0]
    benchmarks = con.execute("SELECT count(*) FROM trade_benchmarks WHERE block_number BETWEEN ? AND ?", [start_block, end_block]).fetchone()[0]
    return prices, benchmarks


def ensure_disk_space(duckdb_path: Path, min_free_gb: float) -> None:
    """Abort loudly before a chunk if the DuckDB volume is close to full."""
    free_gb = shutil.disk_usage(duckdb_path.parent).free / 1e9
    if free_gb < min_free_gb:
        raise RuntimeError(f"Only {free_gb:.1f} GB free on {duckdb_path.parent}, below MIN_FREE_DISK_GB={min_free_gb}. The scan resumes from the last committed block when rerun.")
    if free_gb < min_free_gb * 2:
        logger.warning("Free disk space is getting low: %.1f GB left on %s", free_gb, duckdb_path.parent)


def checkpoint_if_due(con: duckdb.DuckDBPyConnection, duckdb_path: Path, chunks_since_checkpoint: int, checkpoint_every_chunks: int) -> int:
    """Run CHECKPOINT between committed chunks to bound WAL growth and compress storage."""
    if checkpoint_every_chunks <= 0:
        return chunks_since_checkpoint
    chunks_since_checkpoint += 1
    if chunks_since_checkpoint < checkpoint_every_chunks:
        return chunks_since_checkpoint
    started = time.time()
    con.execute("CHECKPOINT")
    main_mb = duckdb_path.stat().st_size / 1e6 if duckdb_path.exists() else 0
    logger.info("Checkpointed in %.1f s: main file %.0f MB", time.time() - started, main_mb)
    return 0


def resolve_config(con: duckdb.DuckDBPyConnection, client: ThrottledHypersyncClient, duckdb_path: Path) -> BenchmarkConfig:
    """Build the run configuration from environment variables and saved state."""
    tip = asyncio.run(client.get_height())
    end_block = int(os.environ.get("END_BLOCK", tip - int(os.environ.get("TIP_SAFETY_BLOCKS", "10"))))
    if "START_BLOCK" in os.environ:
        start_block = int(os.environ["START_BLOCK"])
    else:
        last = read_scan_state(con, "benchmark_last_scanned_block")
        start_block = last + 1 if last is not None else int(os.environ.get("BENCHMARK_START_BLOCK", "50000000"))
    override = [Web3.to_checksum_address(p.strip()) for p in os.environ.get("BENCHMARK_POOLS", "").split(",") if p.strip()]
    return BenchmarkConfig(
        duckdb_path=duckdb_path,
        start_block=start_block,
        end_block=end_block,
        chunk_blocks=int(os.environ.get("CHUNK_BLOCKS", "30000")),
        max_pairs=int(os.environ.get("BENCHMARK_MAX_PAIRS", "8")),
        pool_override=override,
        discovery_lookback_blocks=int(os.environ.get("POOL_DISCOVERY_LOOKBACK_BLOCKS", "20000")),
        checkpoint_every_chunks=int(os.environ.get("CHECKPOINT_EVERY_CHUNKS", "5")),
        min_free_disk_gb=float(os.environ.get("MIN_FREE_DISK_GB", "10")),
        recv_timeout=float(os.environ.get("HYPERSYNC_RECV_TIMEOUT", "120")),
    )


def log_summary(con: duckdb.DuckDBPyConnection, duckdb_path: Path) -> None:
    """Log what the benchmark tables now contain."""
    for table in ("benchmark_pools", "benchmark_swaps", "benchmark_block_prices", "trade_benchmarks"):
        logger.info("Table %s: %d rows", table, con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    for pool, protocol, s0, s1, n, lo, hi in con.execute("""
        SELECT p.pool, p.protocol, p.symbol0, p.symbol1, count(s.block_number), min(s.block_number), max(s.block_number)
        FROM benchmark_pools p LEFT JOIN benchmark_swaps s ON s.pool = p.pool
        GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC
    """).fetchall():
        logger.info("Pool %s %s/%s (%s): %d swaps, blocks %s - %s", pool, s0, s1, protocol, n, f"{lo:,}" if lo else "-", f"{hi:,}" if hi else "-")
    first_block = con.execute("SELECT min(block_number) FROM benchmark_block_prices").fetchone()[0] or 0
    covered = con.execute("SELECT count(*), coalesce(sum(CASE WHEN benchmark_price IS NOT NULL THEN 1 ELSE 0 END), 0) FROM trade_vs_benchmark WHERE block_number > ?", [first_block]).fetchone()
    logger.info("trade_vs_benchmark: %s Tessera trades after block %s, %s with a reference price", f"{covered[0]:,}", f"{first_block:,}", f"{int(covered[1]):,}")
    logger.info("On disk: main file %.0f MB, %.1f GB free", duckdb_path.stat().st_size / 1e6, shutil.disk_usage(duckdb_path.parent).free / 1e9)


def main() -> None:
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    json_rpc_base = os.environ.get("JSON_RPC_BASE")
    assert json_rpc_base, "JSON_RPC_BASE must be set"
    web3 = create_multi_provider_web3(json_rpc_base)
    assert web3.eth.chain_id == CHAIN_ID, f"Expected Base, got chain {web3.eth.chain_id}"

    client = configure_hypersync_from_env(web3).hypersync_client
    assert client is not None, "Hypersync client could not be configured, check HYPERSYNC_API_KEY"

    duckdb_path = Path(os.environ.get("TESSERA_DUCKDB_PATH", "~/.tradingstrategy/tessera/tessera-base.duckdb")).expanduser()
    assert duckdb_path.exists(), f"{duckdb_path} does not exist, run scan-tessera-propamm.py first"
    con = duckdb.connect(str(duckdb_path))
    try:
        create_schema(con)
        config = resolve_config(con, client, duckdb_path)
        logger.info("Benchmark scan blocks %s - %s (%d blocks) into %s", f"{config.start_block:,}", f"{config.end_block:,}", config.end_block - config.start_block + 1, duckdb_path)

        pools = choose_pools(con, web3, client, config)
        assert pools, "No reference pools could be chosen"
        store_pools(con, pools)
        pool_map = {p.address.lower(): p for p in pools}

        if config.end_block >= config.start_block:
            chunk_starts = range(config.start_block, config.end_block + 1, config.chunk_blocks)
            total = 0
            chunks_since_checkpoint = 0
            progress = tqdm(chunk_starts, desc="Benchmark swap scan", unit="chunk")
            for chunk_start in progress:
                ensure_disk_space(duckdb_path, config.min_free_disk_gb)
                chunk_end = min(chunk_start + config.chunk_blocks - 1, config.end_block)
                started = time.time()
                n, pages = scan_swap_chunk(con, client, pool_map, chunk_start, chunk_end, config.recv_timeout)
                total += n
                progress.set_postfix({"block": f"{chunk_end:,}", "swaps": f"{total:,}"})
                logger.info("Blocks %s - %s: %d swaps in %d pages, %.1f s", f"{chunk_start:,}", f"{chunk_end:,}", n, pages, time.time() - started)
                chunks_since_checkpoint = checkpoint_if_due(con, duckdb_path, chunks_since_checkpoint, config.checkpoint_every_chunks)
                pace_pages(pages, started)
            logger.info("Swap scan done: %d swaps", total)
        else:
            logger.info("Nothing new to scan, last benchmarked block is %s", f"{config.start_block - 1:,}")

        # Derived tables cover the whole benchmarked range, so a run that resumed
        # part-way (or REBUILD_START_BLOCK) still rebuilds earlier chunks' rows.
        rebuild_start = int(os.environ.get("REBUILD_START_BLOCK", config.start_block))
        rebuild_end = max(config.end_block, config.start_block - 1)
        if rebuild_end >= rebuild_start:
            prices, benchmarks = rebuild_block_prices(con, rebuild_start, rebuild_end)
            logger.info("Rebuilt %d end-of-block benchmark prices and %d per-trade benchmarks for blocks %s - %s", prices, benchmarks, f"{rebuild_start:,}", f"{rebuild_end:,}")

        log_summary(con, duckdb_path)
    finally:
        con.close()


if __name__ == "__main__":
    main()

"""Collect Tessera propAMM execution-quality data on Base into a DuckDB file.

Tessera is the Wintermute proprietary AMM (propAMM) on Base. This script gathers
everything needed to analyse how much value the venue extracts from retail
swappers through quote-to-settlement repricing and slippage capture, as
documented in the 0x "PropAMM Shenanigans" post:
https://0x.org/post/propamm-shenanigans

Contract topology on Base (chain id 8453):

- ``TesseraSwap`` ``0x5555…9E3e`` — thin verified shell, emits ``TesseraTrade``
  and exposes the ``tesseraSwapViewAmounts()`` view quote.
- ``TesseraEngine`` ``0x31e9…0c17`` — unverified pricing logic.
- Price store ``0xf524…5a7C`` — unverified EIP-1967 proxy. Wintermute keepers
  write packed quotes here several times per block. **No events are emitted**,
  so the updates are only visible as transactions, which is why Hypersync is
  used to stream them.
- Treasury ``0x3dBE…0AaE`` — inventory pulled via ``transferFrom``.

ElfomoFi, the second propAMM on Base, is captured in the same trades table for
comparison.

Data collected (all tables are append-only, deduplicated by block range, and
deliberately have no ``PRIMARY KEY`` or ``UNIQUE`` constraints, see
``CLAUDE.md`` on DuckDB ART index corruption):

- ``blocks`` — timestamp, gas used, base fee for every block that contains a
  trade or a price update.
- ``block_tx_counts`` — transaction count per block, so a transaction's
  relative position in the block (the flashblock proxy) can be computed.
- ``trades`` — decoded ``TesseraTrade`` / ``ElfomoTrade`` events joined with
  their transaction (router, sender, priority fee, cumulative gas position).
- ``price_updates`` — every transaction sent to the price store: sender,
  selector, raw calldata, priority fee, position in block.
- ``trade_calls`` — the inner ``tesseraSwapWith*`` call decoded from a
  ``debug_traceTransaction`` call trace: ``amountSpecified``, ``amountCheck``
  (the slippage bound passed to Tessera), recipient and ``swapData``. Note
  that most aggregators pass ``amountCheck = 0`` and enforce the user's
  slippage at the router level instead; ERC-4337 smart wallet flow is the
  main source of non-zero inner bounds.
- ``trade_orders`` — per inner Tessera call, the aggregator identified on the
  trace call path (``EntryPoint → wallet → router → adapter → Tessera``) and
  the user's order decoded from the router frame with
  :py:mod:`eth_defi.dex_aggregator.router_calldata`: source/destination
  token, input amount, **user minimum output (slippage bound)**, aggregator's
  own quote where recorded (Paraswap), recipient, front-end hints
  (KyberSwap ``clientData``, 0x ``zid``), wallet kind.
- ``trade_tx_inputs`` — the outer transaction target, selector and raw
  calldata taken from the trace root frame, so router-specific ``minReturn``
  decoders (OKX, 1inch, KyberSwap, 0x, ERC-4337 user operations) can be
  added later without refetching. Disable with ``STORE_TX_INPUT=false`` on
  very long backfills to save space.
- ``quotes`` — historical ``tesseraSwapViewAmounts()`` results at the end of
  the previous block and at the end of the trade's own block, for both the
  trade size and a small probe size. Quote-to-fill degradation and size price
  impact are derived from these.
- ``tokens`` — symbol and decimals of every token seen.
- ``trade_analysis`` view — joins the above into per-trade basis point metrics
  ready for a notebook.

Benchmark reference prices (``benchmark_pools``, ``benchmark_swaps``,
``benchmark_block_prices``, ``trade_benchmarks`` and the ``trade_vs_benchmark``
view) live in the same file and are owned by
``scripts/base/scan-tessera-benchmark-prices.py``; run it after this script.

The script is resumable: the last scanned block is stored in ``scan_state``
and each phase only processes rows that lack enrichment.

Usage::

    # First run, last ~1 day of blocks
    source .local-test.env && LOOKBACK_BLOCKS=43200 poetry run python scripts/base/scan-tessera-propamm.py

    # Continue from the last scanned block to the chain tip
    source .local-test.env && poetry run python scripts/base/scan-tessera-propamm.py

    # Explicit range, skip the slow enrichment phases
    source .local-test.env && START_BLOCK=51300000 END_BLOCK=51310000 ENRICH_TRACES=false ENRICH_QUOTES=false poetry run python scripts/base/scan-tessera-propamm.py

    # Full history: raw streams first, then enrich the last ~30 days in a second run
    source .local-test.env && START_BLOCK=37518780 ENRICH_TRACES=false ENRICH_QUOTES=false poetry run python scripts/base/scan-tessera-propamm.py
    source .local-test.env && ENRICH_START_BLOCK=50000000 MAX_WORKERS=16 poetry run python scripts/base/scan-tessera-propamm.py

Environment variables:

- ``JSON_RPC_BASE`` — archive-capable Base JSON-RPC (space separated fallbacks
  ok). Must support ``debug_traceTransaction`` for the trace phase.
- ``HYPERSYNC_API_KEY`` — Envio Hypersync key.
- ``TESSERA_DUCKDB_PATH`` — output database, default
  ``~/.tradingstrategy/tessera/tessera-base.duckdb``.
- ``START_BLOCK`` / ``END_BLOCK`` — explicit block range. ``START_BLOCK``
  defaults to the saved scan state, else ``END_BLOCK - LOOKBACK_BLOCKS``.
  ``END_BLOCK`` defaults to the Hypersync tip minus ``TIP_SAFETY_BLOCKS``.
- ``LOOKBACK_BLOCKS`` — default ``43200`` (one day of 2 s blocks).
- ``CHUNK_BLOCKS`` — blocks per Hypersync query and DuckDB commit, default ``10000``.
- ``CHECKPOINT_EVERY_CHUNKS`` — run an explicit ``CHECKPOINT`` after this many
  committed chunks, default ``20`` (~200,000 blocks). A checkpoint flushes the
  WAL into the main file using DuckDB's automatic per-column compression and
  truncates the WAL, so disk usage stays bounded on a long backfill instead of
  growing into one multi-day, mostly-uncompressed WAL file. Checkpoints only
  run between committed chunks, never mid-transaction, so this is safe
  alongside the no-ART-index convention in ``CLAUDE.md`` (the corruption bug
  it warns about is tied to ART-backed ``PRIMARY KEY``/``UNIQUE`` indexes,
  which this schema does not use). Set to ``0`` to disable and only checkpoint
  once at clean shutdown.
- ``MIN_FREE_DISK_GB`` — abort before starting a chunk if free disk space on
  the DuckDB volume drops below this, default ``10``.
- ``SCAN_BLOCK_TX_COUNTS`` — stream all Base transactions (two integer
  columns) to get per-block transaction counts, default ``false``. The
  gas-based position ``cumulative_gas_used / block_gas_used`` is always
  available and is the better flashblock proxy, so this is only needed when
  the transaction-index position is wanted. Very expensive on a full scan.
- ``ENRICH_START_BLOCK`` — only trace and quote trades at or after this
  block, default ``0`` (all). Use it to bound the slow RPC phases on a full
  history scan; the phases are resumable and pick up remaining trades later.
- ``HYPERSYNC_RECV_TIMEOUT`` — seconds to wait for one stream response, default ``120``.
- ``HYPERSYNC_MAX_ATTEMPTS`` — retries per Hypersync stream chunk before the
  script raises and exits, default ``10``. Base's Hypersync endpoint has
  frequent transient blips (connection resets, timeouts, brief server-side
  rate limiting) on multi-hour scans; the process is always safely resumable
  via ``scan_state`` if it does exit, but raising this reduces how often a
  full history backfill needs a manual restart.
- ``HYPERSYNC_MAX_BACKOFF_SECONDS`` — cap on the exponential backoff between
  retries, default ``120``.
- ``ENRICH_TRACES`` — run ``debug_traceTransaction`` per Tessera trade, default ``true``.
  Budget roughly 10-40 transactions per second depending on ``MAX_WORKERS``.
- ``STORE_TX_INPUT`` — keep the outer transaction calldata from traces, default ``true``.
- ``ENRICH_QUOTES`` — run historical multicall quotes per trade, default ``true``.
  Two archive ``eth_call`` multicalls per block that contains trades.
- ``PROBE_FRACTION`` — probe size as a fraction of the trade size for price
  impact, default ``0.01``.
- ``MAX_WORKERS`` — thread count for RPC enrichment phases, default ``8``.
- ``LOG_LEVEL`` — default ``info``.
"""

import asyncio
import datetime
import json
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
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.contract import Contract

import hypersync
from hypersync import BlockField, LogField, TransactionField

from eth_defi.event_reader.multicall_batcher import get_multicall_contract
from eth_defi.hypersync.hypersync_timestamp import is_hypersync_retryable_runtime_error
from eth_defi.hypersync.session import ThrottledHypersyncClient, open_hypersync_stream
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.utils import setup_console_logging
from eth_defi.compat import native_datetime_utc_now
from eth_defi.dex_aggregator.router_calldata import identify_call_path
from eth_defi.vault.flow_events import decode_hypersync_int

logger = logging.getLogger(__name__)

CHAIN_ID = 8453

#: TesseraSwap shell contract, verified on Sourcify
TESSERA_SWAP: HexAddress = "0x55555522005BcAE1c2424D474BfD5ed477749E3e"

#: Unverified TesseraEngine pricing contract
TESSERA_ENGINE: HexAddress = "0x31e99E05fee3DCE580af777C3fD63eE1B3B40c17"

#: Unverified EIP-1967 proxy the engine reads its quotes from; keepers write here every block
TESSERA_PRICE_STORE: HexAddress = "0xf524C1Bc1C64A2C99bc7eccf19EDe9a1d89d5a7C"

#: Wintermute inventory wallet
TESSERA_TREASURY: HexAddress = "0x3dBE077e7986657E95e1CC50089f17a5a4AF0AaE"

#: Block where the price store was deployed, 2025-10-30
TESSERA_DEPLOY_BLOCK = 37_518_780

#: ElfomoFi propAMM swap contract
ELFOMO_SWAP: HexAddress = "0xf0f0F0F0FB0d738452EfD03A28e8be14C76d5f73"

#: TesseraTrade(address tokenIn, address tokenOut, uint256 amountIn, uint256 amountOut, address recipient)
TESSERA_TRADE_TOPIC = "0x97ba0cd8ff13f074b3b1aeace7fa3bf7fe54bdf2d728b6a097e901073b2bad6a"

#: ElfomoTrade(uint256 indexed quoteId, uint256 indexed partnerId, address executor, address receiver, address fromToken, address toToken, uint256 fromAmount, uint256 toAmount)
ELFOMO_TRADE_TOPIC = "0xbe65a3f1f381da16732df786f571604a72b7c122cff3ae2b355566ddf01e2528"

#: tesseraSwapWithAllowances(address,address,int256,uint256,address,bytes)
SELECTOR_SWAP_WITH_ALLOWANCES = "0x3ae8b298"

#: tesseraSwapWithCallback(address,address,int256,uint256,address,bytes,bytes)
SELECTOR_SWAP_WITH_CALLBACK = "0x15b8527c"

#: tesseraSwapViewAmounts(address,address,int256)
SELECTOR_VIEW_AMOUNTS = Web3.keccak(text="tesseraSwapViewAmounts(address,address,int256)")[0:4]

#: DuckDB HUGEINT upper bound; token amounts above this are stored as NULL
HUGEINT_MAX = 2**127 - 1

#: Hypersync stream retry budget. Base's Hypersync deployment sees frequent
#: transient network blips (connection resets, timeouts, occasional server-side
#: rate limiting) during multi-hour scans; override with HYPERSYNC_MAX_ATTEMPTS
#: if a run keeps exhausting this and crashing.
HYPERSYNC_MAX_ATTEMPTS = int(os.environ.get("HYPERSYNC_MAX_ATTEMPTS", "10"))

#: Cap on the exponential backoff between retries, seconds
HYPERSYNC_MAX_BACKOFF_SECONDS = int(os.environ.get("HYPERSYNC_MAX_BACKOFF_SECONDS", "120"))


@dataclass(slots=True)
class ScanConfig:
    """Runtime configuration parsed from environment variables."""

    #: Output DuckDB file
    duckdb_path: Path
    #: First block to scan, inclusive
    start_block: int
    #: Last block to scan, inclusive
    end_block: int
    #: Blocks per Hypersync query
    chunk_blocks: int
    #: Run CHECKPOINT after this many committed chunks, 0 disables periodic checkpointing
    checkpoint_every_chunks: int
    #: Abort a chunk if free disk space drops below this many GB
    min_free_disk_gb: float
    #: Stream all transactions for per-block counts
    scan_block_tx_counts: bool
    #: Run debug_traceTransaction enrichment
    enrich_traces: bool
    #: Store outer transaction calldata from traces
    store_tx_input: bool
    #: Run historical quote enrichment
    enrich_quotes: bool
    #: Probe size fraction for price impact quotes
    probe_fraction: float
    #: Threads for RPC phases
    max_workers: int
    #: Only enrich trades at or after this block
    enrich_start_block: int
    #: Seconds to wait for one Hypersync stream response
    recv_timeout: float


def hugeint_str(value: int | None) -> str | None:
    """Convert a uint256/int256 value to a HUGEINT-safe string, clamping overflow to NULL.

    Solidity contracts and aggregator calldata commonly use
    ``type(uint256).max`` as a sentinel for "no bound" (an unlimited slippage
    check, an unlimited approval) -- 2^256-1, far beyond DuckDB HUGEINT's
    signed 128-bit range. This crashed a live scan
    (``_duckdb.ConversionException`` on ``amount_check`` from an aggregator
    router's "no minimum" sentinel, 2026-09-16). Storing NULL with a logged
    warning keeps the overflow auditable without corrupting the value or
    crashing the insert. Apply this to every uint256/int256 field before it
    reaches a HUGEINT column, not just the ones already known to overflow --
    the same sentinel pattern shows up in different aggregators' calldata.
    """
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


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create tables and analysis view if they do not exist.

    Large tables intentionally have no ``PRIMARY KEY``/``UNIQUE`` constraints.
    """
    # Defer DuckDB's *automatic* mid-write checkpoint trigger (CLAUDE.md: bulk
    # ingestion safeguard against ART-index heap corruption). We still run
    # explicit, controlled CHECKPOINTs between committed chunks (see
    # checkpoint_if_due()) to bound WAL growth and get compression -- that is
    # a deliberate operation between transactions, not the automatic
    # mid-write trigger this setting defers.
    con.execute("SET wal_autocheckpoint = '1TB'")
    con.execute("""
        CREATE TABLE IF NOT EXISTS scan_state (
            key VARCHAR,
            value BIGINT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS blocks (
            block_number BIGINT,
            timestamp TIMESTAMP,
            gas_used BIGINT,
            base_fee_per_gas BIGINT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS block_tx_counts (
            block_number BIGINT,
            tx_count INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            venue VARCHAR,
            block_number BIGINT,
            timestamp TIMESTAMP,
            tx_hash VARCHAR,
            tx_index INTEGER,
            log_index INTEGER,
            token_in VARCHAR,
            token_out VARCHAR,
            amount_in HUGEINT,
            amount_out HUGEINT,
            recipient VARCHAR,
            quote_id VARCHAR,
            partner_id VARCHAR,
            tx_from VARCHAR,
            tx_to VARCHAR,
            tx_value HUGEINT,
            tx_selector VARCHAR,
            gas_used BIGINT,
            cumulative_gas_used BIGINT,
            block_gas_used BIGINT,
            max_priority_fee_per_gas BIGINT,
            max_fee_per_gas BIGINT,
            effective_gas_price BIGINT,
            status INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS price_updates (
            block_number BIGINT,
            timestamp TIMESTAMP,
            tx_hash VARCHAR,
            tx_index INTEGER,
            tx_from VARCHAR,
            selector VARCHAR,
            input VARCHAR,
            input_length INTEGER,
            tx_value HUGEINT,
            gas_used BIGINT,
            cumulative_gas_used BIGINT,
            block_gas_used BIGINT,
            max_priority_fee_per_gas BIGINT,
            max_fee_per_gas BIGINT,
            effective_gas_price BIGINT,
            status INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_calls (
            tx_hash VARCHAR,
            call_ordinal INTEGER,
            call_depth INTEGER,
            caller VARCHAR,
            selector VARCHAR,
            token_in VARCHAR,
            token_out VARCHAR,
            amount_specified HUGEINT,
            exact_input BOOLEAN,
            amount_check HUGEINT,
            recipient VARCHAR,
            swap_data VARCHAR,
            callback_data VARCHAR,
            reverted BOOLEAN
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_orders (
            tx_hash VARCHAR,
            call_ordinal INTEGER,
            aggregator VARCHAR,
            frontend VARCHAR,
            wallet_kind VARCHAR,
            path_labels VARCHAR,
            path_depth INTEGER,
            router VARCHAR,
            router_function VARCHAR,
            router_frame_depth INTEGER,
            order_src_token VARCHAR,
            order_dst_token VARCHAR,
            order_amount_in HUGEINT,
            user_min_amount_out HUGEINT,
            user_max_amount_in HUGEINT,
            aggregator_quoted_out HUGEINT,
            order_recipient VARCHAR,
            order_deadline BIGINT,
            client_data VARCHAR,
            exact_output BOOLEAN,
            router_returned_out HUGEINT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_tx_inputs (
            tx_hash VARCHAR,
            tx_to VARCHAR,
            selector VARCHAR,
            input VARCHAR,
            input_length INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_trace_state (
            tx_hash VARCHAR,
            traced_at TIMESTAMP,
            error VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            tx_hash VARCHAR,
            log_index INTEGER,
            quote_block BIGINT,
            kind VARCHAR,
            size VARCHAR,
            amount_specified HUGEINT,
            quoted_amount_in HUGEINT,
            quoted_amount_out HUGEINT,
            success BOOLEAN
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            address VARCHAR,
            symbol VARCHAR,
            decimals INTEGER
        )
    """)
    con.execute("""
        CREATE OR REPLACE VIEW trade_analysis AS
        WITH q AS (
            SELECT
                tx_hash,
                log_index,
                MAX(CASE WHEN kind = 'prev' AND size = 'trade' THEN quoted_amount_out END) AS prev_quoted_out,
                MAX(CASE WHEN kind = 'prev' AND size = 'probe' THEN quoted_amount_in END) AS prev_probe_in,
                MAX(CASE WHEN kind = 'prev' AND size = 'probe' THEN quoted_amount_out END) AS prev_probe_out,
                MAX(CASE WHEN kind = 'same' AND size = 'trade' THEN quoted_amount_out END) AS same_quoted_out
            FROM quotes
            WHERE success
            GROUP BY tx_hash, log_index
        ),
        c AS (
            -- Match the n-th inner Tessera call in a transaction to the n-th TesseraTrade log
            SELECT tx_hash, call_ordinal, amount_check, exact_input, caller, swap_data
            FROM trade_calls
            WHERE NOT reverted
        ),
        o AS (
            SELECT
                *,
                -- Aggregators use 0x0 / 0xEeee for native ETH; the Tessera leg always sees WETH
                CASE WHEN lower(order_dst_token) IN ('0x0000000000000000000000000000000000000000', '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee')
                     THEN '0x4200000000000000000000000000000000000006' ELSE order_dst_token END AS order_dst_token_norm,
                CASE WHEN lower(order_src_token) IN ('0x0000000000000000000000000000000000000000', '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee')
                     THEN '0x4200000000000000000000000000000000000006' ELSE order_src_token END AS order_src_token_norm
            FROM trade_orders
        ),
        t AS (
            SELECT
                t.*,
                ROW_NUMBER() OVER (PARTITION BY t.tx_hash ORDER BY t.log_index) - 1 AS call_ordinal
            FROM trades t
        )
        SELECT
            t.venue,
            t.block_number,
            t.timestamp,
            t.tx_hash,
            t.tx_index,
            t.log_index,
            btc.tx_count,
            (t.tx_index + 1.0) / btc.tx_count AS rel_tx_position,
            t.cumulative_gas_used * 1.0 / NULLIF(t.block_gas_used, 0) AS rel_gas_position,
            t.token_in,
            ti.symbol AS symbol_in,
            t.token_out,
            tok.symbol AS symbol_out,
            t.amount_in / POWER(10, ti.decimals) AS amount_in_decimal,
            t.amount_out / POWER(10, tok.decimals) AS amount_out_decimal,
            (t.amount_out / POWER(10, tok.decimals)) / NULLIF(t.amount_in / POWER(10, ti.decimals), 0) AS fill_price,
            t.tx_from,
            t.tx_to AS router,
            -- EIP-7702 delegated EOA calling itself: almost always a bot, not retail
            t.tx_from = t.tx_to AS is_self_call,
            t.max_priority_fee_per_gas,
            c.caller AS inner_caller,
            c.exact_input,
            c.amount_check,
            o.aggregator,
            o.frontend,
            o.wallet_kind,
            o.path_labels,
            o.router_function,
            o.order_src_token,
            o.order_dst_token,
            o.order_amount_in,
            o.user_min_amount_out,
            o.aggregator_quoted_out,
            o.client_data,
            -- The user's bound is only attributable to this Tessera leg when the order's output token is the leg's
            -- output token and the order was not split across venues (input amount equal, or unknown for 0x)
            o.router_returned_out,
            (o.order_dst_token_norm = t.token_out AND (o.order_src_token_norm IS NULL OR o.order_src_token_norm = t.token_in)
             AND (o.order_amount_in IS NULL OR (t.amount_in BETWEEN o.order_amount_in * 0.98 AND o.order_amount_in))) AS order_matches_leg,
            q.prev_quoted_out,
            q.same_quoted_out,
            q.prev_probe_in,
            q.prev_probe_out,
            -- Quote at end of previous block vs actual fill; positive = user got less than quoted
            (q.prev_quoted_out - t.amount_out) * 10000.0 / NULLIF(q.prev_quoted_out, 0) AS quote_to_fill_bps,
            -- Quote at end of the trade's own block vs actual fill; shows repricing after the fill
            (q.same_quoted_out - t.amount_out) * 10000.0 / NULLIF(q.same_quoted_out, 0) AS same_block_quote_to_fill_bps,
            -- Size price impact at the previous block: trade-size effective price vs probe-size effective price
            (1 - (q.prev_quoted_out * 1.0 / NULLIF(t.amount_in, 0)) / NULLIF(q.prev_probe_out * 1.0 / NULLIF(q.prev_probe_in, 0), 0)) * 10000.0 AS size_impact_bps,
            -- Slippage bound the aggregator/user gave, relative to the previous block quote
            CASE WHEN c.exact_input THEN (q.prev_quoted_out - c.amount_check) * 10000.0 / NULLIF(q.prev_quoted_out, 0) END AS given_slippage_bps,
            -- Fraction of the given slippage that the fill actually consumed
            CASE WHEN c.exact_input THEN (q.prev_quoted_out - t.amount_out) * 1.0 / NULLIF(q.prev_quoted_out - c.amount_check, 0) END AS slippage_consumed_fraction,
            -- Distance between fill and the bound, in bps of the bound
            CASE WHEN c.exact_input THEN (t.amount_out - c.amount_check) * 10000.0 / NULLIF(c.amount_check, 0) END AS headroom_to_bound_bps,
            -- User-level slippage tolerance from the aggregator router, relative to the previous block Tessera quote
            CASE WHEN o.order_dst_token_norm = t.token_out AND (o.order_src_token_norm IS NULL OR o.order_src_token_norm = t.token_in) AND (o.order_amount_in IS NULL OR (t.amount_in BETWEEN o.order_amount_in * 0.98 AND o.order_amount_in)) AND NOT COALESCE(o.exact_output, FALSE)
                 THEN (q.prev_quoted_out - o.user_min_amount_out) * 10000.0 / NULLIF(q.prev_quoted_out, 0) END AS user_slippage_bps,
            -- Fraction of the user's tolerance the fill consumed
            CASE WHEN o.order_dst_token_norm = t.token_out AND (o.order_src_token_norm IS NULL OR o.order_src_token_norm = t.token_in) AND (o.order_amount_in IS NULL OR (t.amount_in BETWEEN o.order_amount_in * 0.98 AND o.order_amount_in)) AND NOT COALESCE(o.exact_output, FALSE)
                 THEN (q.prev_quoted_out - t.amount_out) * 1.0 / NULLIF(q.prev_quoted_out - o.user_min_amount_out, 0) END AS user_slippage_consumed_fraction,
            -- Distance between the fill and the user's bound, in bps of the bound; ~0 means the user got exactly their worst case
            CASE WHEN o.order_dst_token_norm = t.token_out AND (o.order_src_token_norm IS NULL OR o.order_src_token_norm = t.token_in) AND (o.order_amount_in IS NULL OR (t.amount_in BETWEEN o.order_amount_in * 0.98 AND o.order_amount_in)) AND NOT COALESCE(o.exact_output, FALSE)
                 THEN (t.amount_out - o.user_min_amount_out) * 10000.0 / NULLIF(o.user_min_amount_out, 0) END AS user_headroom_bps,
            -- Aggregator's own quote vs fill, where the router records the quote (Paraswap)
            CASE WHEN o.order_dst_token_norm = t.token_out AND (o.order_src_token_norm IS NULL OR o.order_src_token_norm = t.token_in) AND (o.order_amount_in IS NULL OR (t.amount_in BETWEEN o.order_amount_in * 0.98 AND o.order_amount_in))
                 THEN (o.aggregator_quoted_out - t.amount_out) * 10000.0 / NULLIF(o.aggregator_quoted_out, 0) END AS aggregator_quote_to_fill_bps,
            -- Whole-route outcome, valid for multi-hop routes too: how far above the user's worst case the router delivered
            CASE WHEN NOT COALESCE(o.exact_output, FALSE)
                 THEN (o.router_returned_out - o.user_min_amount_out) * 10000.0 / NULLIF(o.user_min_amount_out, 0) END AS user_route_headroom_bps,
            -- Whole-route aggregator quote vs delivered, where the router records its quote (Paraswap)
            CASE WHEN NOT COALESCE(o.exact_output, FALSE)
                 THEN (o.aggregator_quoted_out - o.router_returned_out) * 10000.0 / NULLIF(o.aggregator_quoted_out, 0) END AS user_route_quote_to_fill_bps
        FROM t
        LEFT JOIN block_tx_counts btc ON btc.block_number = t.block_number
        LEFT JOIN tokens ti ON ti.address = t.token_in
        LEFT JOIN tokens tok ON tok.address = t.token_out
        LEFT JOIN q ON q.tx_hash = t.tx_hash AND q.log_index = t.log_index
        LEFT JOIN c ON c.tx_hash = t.tx_hash AND c.call_ordinal = t.call_ordinal
        LEFT JOIN o ON o.tx_hash = t.tx_hash AND o.call_ordinal = t.call_ordinal
    """)
    con.execute("""
        CREATE OR REPLACE VIEW price_update_analysis AS
        SELECT
            p.*,
            btc.tx_count,
            (p.tx_index + 1.0) / btc.tx_count AS rel_tx_position,
            p.cumulative_gas_used * 1.0 / NULLIF(p.block_gas_used, 0) AS rel_gas_position
        FROM price_updates p
        LEFT JOIN block_tx_counts btc ON btc.block_number = p.block_number
    """)


def read_scan_state(con: duckdb.DuckDBPyConnection, key: str) -> int | None:
    """Read a scan progress marker."""
    row = con.execute("SELECT value FROM scan_state WHERE key = ?", [key]).fetchone()
    return int(row[0]) if row else None


def write_scan_state(con: duckdb.DuckDBPyConnection, key: str, value: int) -> None:
    """Upsert a scan progress marker."""
    con.execute("DELETE FROM scan_state WHERE key = ?", [key])
    con.execute("INSERT INTO scan_state VALUES (?, ?)", [key, value])


async def _collect_stream(
    client: ThrottledHypersyncClient,
    query: hypersync.Query,
    progress: tqdm | None,
    recv_timeout: float,
) -> tuple[list, list, list]:
    """Drain a Hypersync stream into (blocks, transactions, logs) lists.

    Advances the progress bar by blocks covered after every response, so a
    long chunk never goes silent.
    """
    receiver = await open_hypersync_stream(client, query)
    blocks, txs, logs = [], [], []
    last_block = query.from_block
    while True:
        res = await asyncio.wait_for(receiver.recv(), timeout=recv_timeout)
        if res is None:
            break
        blocks += res.data.blocks or []
        txs += res.data.transactions or []
        logs += res.data.logs or []
        if progress is not None and res.next_block is not None:
            progress.update(res.next_block - last_block)
            progress.set_postfix({"logs": f"{len(logs):,}", "txs": f"{len(txs):,}"})
            last_block = res.next_block
    if progress is not None and query.to_block > last_block:
        progress.update(query.to_block - last_block)
    return blocks, txs, logs


def collect_stream(
    client: ThrottledHypersyncClient,
    query: hypersync.Query,
    context: str,
    recv_timeout: float,
) -> tuple[list, list, list]:
    """Run a Hypersync query synchronously with a progress bar and retries for rate limits.

    :param context:
        Human readable label for the progress bar and log messages.
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


def build_activity_query(start_block: int, end_block: int) -> hypersync.Query:
    """Trade logs from both venues plus every transaction sent to the price store.

    Default join mode pulls the transaction and block for each log, and the
    block for each selected transaction.
    """
    return hypersync.Query(
        from_block=start_block,
        to_block=end_block + 1,
        logs=[
            hypersync.LogSelection(address=[TESSERA_SWAP.lower()], topics=[[TESSERA_TRADE_TOPIC]]),
            hypersync.LogSelection(address=[ELFOMO_SWAP.lower()], topics=[[ELFOMO_TRADE_TOPIC]]),
        ],
        transactions=[
            hypersync.TransactionSelection(to=[TESSERA_PRICE_STORE.lower()]),
        ],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP, BlockField.GAS_USED, BlockField.BASE_FEE_PER_GAS],
            log=[
                LogField.BLOCK_NUMBER,
                LogField.LOG_INDEX,
                LogField.TRANSACTION_HASH,
                LogField.TRANSACTION_INDEX,
                LogField.ADDRESS,
                LogField.TOPIC0,
                LogField.TOPIC1,
                LogField.TOPIC2,
                LogField.DATA,
            ],
            transaction=[
                TransactionField.BLOCK_NUMBER,
                TransactionField.TRANSACTION_INDEX,
                TransactionField.HASH,
                TransactionField.FROM,
                TransactionField.TO,
                TransactionField.INPUT,
                TransactionField.VALUE,
                TransactionField.GAS_USED,
                TransactionField.CUMULATIVE_GAS_USED,
                TransactionField.MAX_PRIORITY_FEE_PER_GAS,
                TransactionField.MAX_FEE_PER_GAS,
                TransactionField.EFFECTIVE_GAS_PRICE,
                TransactionField.STATUS,
            ],
        ),
    )


def build_tx_count_query(start_block: int, end_block: int) -> hypersync.Query:
    """Every transaction in range, two integer columns only, to count per block."""
    return hypersync.Query(
        from_block=start_block,
        to_block=end_block + 1,
        transactions=[hypersync.TransactionSelection()],
        join_mode=hypersync.JoinMode.JOIN_NOTHING,
        field_selection=hypersync.FieldSelection(
            transaction=[TransactionField.BLOCK_NUMBER, TransactionField.TRANSACTION_INDEX],
        ),
    )


def decode_trade_log(log, tx, block) -> dict:
    """Decode a TesseraTrade or ElfomoTrade log into a trades row."""
    data = to_bytes(log.data)
    topic0 = log.topics[0]
    if topic0 == TESSERA_TRADE_TOPIC:
        token_in, token_out, amount_in, amount_out, recipient = eth_abi.decode(["address", "address", "uint256", "uint256", "address"], data)
        venue = "tessera"
        quote_id = partner_id = None
    elif topic0 == ELFOMO_TRADE_TOPIC:
        _executor, recipient, token_in, token_out, amount_in, amount_out = eth_abi.decode(["address", "address", "address", "address", "uint256", "uint256"], data)
        venue = "elfomo"
        quote_id = hex_int(log.topics[1])
        partner_id = hex_int(log.topics[2])
    else:
        raise AssertionError(f"Unexpected topic {topic0}")

    tx_input = tx.input or "0x"
    return {
        "venue": venue,
        "block_number": hex_int(log.block_number),
        "timestamp": datetime.datetime.fromtimestamp(hex_int(block.timestamp), tz=datetime.timezone.utc).replace(tzinfo=None),
        "tx_hash": log.transaction_hash,
        "tx_index": hex_int(log.transaction_index),
        "log_index": hex_int(log.log_index),
        "token_in": Web3.to_checksum_address(token_in),
        "token_out": Web3.to_checksum_address(token_out),
        "amount_in": hugeint_str(amount_in),
        "amount_out": hugeint_str(amount_out),
        "recipient": Web3.to_checksum_address(recipient),
        "quote_id": str(quote_id) if quote_id is not None else None,
        "partner_id": str(partner_id) if partner_id is not None else None,
        "tx_from": Web3.to_checksum_address(tx.from_) if tx.from_ else None,
        "tx_to": Web3.to_checksum_address(tx.to) if tx.to else None,
        "tx_value": str(hex_int(tx.value) or 0),
        "tx_selector": tx_input[:10] if len(tx_input) >= 10 else None,
        "gas_used": hex_int(tx.gas_used),
        "cumulative_gas_used": hex_int(tx.cumulative_gas_used),
        "block_gas_used": hex_int(block.gas_used),
        "max_priority_fee_per_gas": hex_int(tx.max_priority_fee_per_gas),
        "max_fee_per_gas": hex_int(tx.max_fee_per_gas),
        "effective_gas_price": hex_int(tx.effective_gas_price),
        "status": hex_int(tx.status),
    }


def decode_price_update(tx, block) -> dict:
    """Turn a transaction to the price store into a price_updates row."""
    tx_input = tx.input or "0x"
    return {
        "block_number": hex_int(tx.block_number),
        "timestamp": datetime.datetime.fromtimestamp(hex_int(block.timestamp), tz=datetime.timezone.utc).replace(tzinfo=None),
        "tx_hash": tx.hash,
        "tx_index": hex_int(tx.transaction_index),
        "tx_from": Web3.to_checksum_address(tx.from_) if tx.from_ else None,
        "selector": tx_input[:10] if len(tx_input) >= 10 else None,
        "input": tx_input,
        "input_length": (len(tx_input) - 2) // 2,
        "tx_value": str(hex_int(tx.value) or 0),
        "gas_used": hex_int(tx.gas_used),
        "cumulative_gas_used": hex_int(tx.cumulative_gas_used),
        "block_gas_used": hex_int(block.gas_used),
        "max_priority_fee_per_gas": hex_int(tx.max_priority_fee_per_gas),
        "max_fee_per_gas": hex_int(tx.max_fee_per_gas),
        "effective_gas_price": hex_int(tx.effective_gas_price),
        "status": hex_int(tx.status),
    }


def insert_frame(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame, columns: list[str]) -> None:
    """Insert a DataFrame with explicit column order so string amounts cast to HUGEINT."""
    if df.empty:
        return
    con.register("_incoming", df[columns])
    col_list = ", ".join(columns)
    con.execute(f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM _incoming")
    con.unregister("_incoming")


def scan_activity_chunk(
    con: duckdb.DuckDBPyConnection,
    client: ThrottledHypersyncClient,
    start_block: int,
    end_block: int,
    scan_block_tx_counts: bool,
    recv_timeout: float,
) -> tuple[int, int]:
    """Scan one block range and commit trades, price updates, blocks and tx counts.

    Existing rows in the range are deleted first so reruns are idempotent.

    :return:
        Tuple (trades inserted, price updates inserted)
    """
    blocks, txs, logs = collect_stream(client, build_activity_query(start_block, end_block), f"activity {start_block:,}-{end_block:,}", recv_timeout)

    block_by_number = {hex_int(b.number): b for b in blocks}
    tx_by_hash = {t.hash: t for t in txs}

    trade_rows = []
    for log in logs:
        tx = tx_by_hash.get(log.transaction_hash)
        block = block_by_number.get(hex_int(log.block_number))
        if tx is None or block is None:
            logger.warning("Missing joined tx/block for log %s in block %s", log.transaction_hash, log.block_number)
            continue
        trade_rows.append(decode_trade_log(log, tx, block))

    update_rows = []
    for tx in txs:
        if tx.to and tx.to.lower() == TESSERA_PRICE_STORE.lower():
            block = block_by_number.get(hex_int(tx.block_number))
            if block is None:
                logger.warning("Missing joined block for price update %s", tx.hash)
                continue
            update_rows.append(decode_price_update(tx, block))

    block_rows = [
        {
            "block_number": hex_int(b.number),
            "timestamp": datetime.datetime.fromtimestamp(hex_int(b.timestamp), tz=datetime.timezone.utc).replace(tzinfo=None),
            "gas_used": hex_int(b.gas_used),
            "base_fee_per_gas": hex_int(b.base_fee_per_gas),
        }
        for b in blocks
    ]

    count_rows = []
    if scan_block_tx_counts:
        _, all_txs, _ = collect_stream(client, build_tx_count_query(start_block, end_block), f"tx counts {start_block:,}-{end_block:,}", recv_timeout)
        max_index: dict[int, int] = {}
        for t in all_txs:
            bn = hex_int(t.block_number)
            idx = hex_int(t.transaction_index)
            if idx > max_index.get(bn, -1):
                max_index[bn] = idx
        count_rows = [{"block_number": bn, "tx_count": idx + 1} for bn, idx in max_index.items()]

    con.begin()
    for table in ("trades", "price_updates", "blocks", "block_tx_counts"):
        con.execute(f"DELETE FROM {table} WHERE block_number BETWEEN ? AND ?", [start_block, end_block])

    trade_columns = [
        "venue", "block_number", "timestamp", "tx_hash", "tx_index", "log_index", "token_in", "token_out",
        "amount_in", "amount_out", "recipient", "quote_id", "partner_id", "tx_from", "tx_to", "tx_value", "tx_selector",
        "gas_used", "cumulative_gas_used", "block_gas_used", "max_priority_fee_per_gas", "max_fee_per_gas",
        "effective_gas_price", "status",
    ]  # fmt: skip
    update_columns = [
        "block_number", "timestamp", "tx_hash", "tx_index", "tx_from", "selector", "input", "input_length", "tx_value",
        "gas_used", "cumulative_gas_used", "block_gas_used", "max_priority_fee_per_gas", "max_fee_per_gas",
        "effective_gas_price", "status",
    ]  # fmt: skip
    insert_frame(con, "trades", pd.DataFrame(trade_rows), trade_columns)
    insert_frame(con, "price_updates", pd.DataFrame(update_rows), update_columns)
    insert_frame(con, "blocks", pd.DataFrame(block_rows), ["block_number", "timestamp", "gas_used", "base_fee_per_gas"])
    insert_frame(con, "block_tx_counts", pd.DataFrame(count_rows), ["block_number", "tx_count"])
    write_scan_state(con, "last_scanned_block", end_block)
    con.commit()
    return len(trade_rows), len(update_rows)


def decode_inner_call(call: dict, depth: int, ordinal: int) -> dict | None:
    """Decode a tesseraSwapWith* call frame from a callTracer trace.

    A handful of real traces carry truncated ``input`` for this frame -- seen
    with one RPC provider under ``create_multi_provider_web3()`` fallback,
    presumably a trace-response size cap on large calldata. The 4-byte
    selector still matches but the remaining bytes are short for the ABI
    shape, so :py:func:`eth_abi.decode` raises. Skip and log rather than
    crashing the whole joblib batch (and, unhandled, the whole scan).
    """
    inp = call.get("input") or "0x"
    selector = inp[:10]
    try:
        if selector == SELECTOR_SWAP_WITH_ALLOWANCES:
            token_in, token_out, amount_specified, amount_check, recipient, swap_data = eth_abi.decode(
                ["address", "address", "int256", "uint256", "address", "bytes"],
                to_bytes(inp)[4:],
            )
            callback_data = b""
        elif selector == SELECTOR_SWAP_WITH_CALLBACK:
            token_in, token_out, amount_specified, amount_check, recipient, callback_data, swap_data = eth_abi.decode(
                ["address", "address", "int256", "uint256", "address", "bytes", "bytes"],
                to_bytes(inp)[4:],
            )
        else:
            return None
    except eth_abi.exceptions.DecodingError as e:
        logger.warning("Cannot decode inner Tessera call, likely a truncated trace (input %d bytes): %s", (len(inp) - 2) // 2, e)
        return None

    return {
        "call_ordinal": ordinal,
        "call_depth": depth,
        "caller": Web3.to_checksum_address(call["from"]),
        "selector": selector,
        "token_in": Web3.to_checksum_address(token_in),
        "token_out": Web3.to_checksum_address(token_out),
        "amount_specified": hugeint_str(abs(amount_specified)),
        "exact_input": amount_specified > 0,
        "amount_check": hugeint_str(amount_check),
        "recipient": Web3.to_checksum_address(recipient),
        "swap_data": "0x" + swap_data.hex(),
        "callback_data": "0x" + callback_data.hex(),
        "reverted": bool(call.get("error")),
    }


def identify_order(tx_hash: str, ordinal: int, path: list[dict], tx_from: str | None, tx_to: str | None) -> dict:
    """Turn the call path from the root frame to a Tessera call into a trade_orders row."""
    frames = [(frame.get("to"), frame.get("input"), frame.get("output")) for frame in path]
    ident = identify_call_path(frames, tx_from=tx_from, tx_to=tx_to)
    order = ident.order
    return {
        "tx_hash": tx_hash,
        "call_ordinal": ordinal,
        "aggregator": ident.aggregator,
        "frontend": ident.frontend,
        "wallet_kind": ident.wallet_kind,
        "path_labels": json.dumps(ident.labels),
        "path_depth": len(path) - 1,
        "router": order.router if order else None,
        "router_function": order.function if order else None,
        "router_frame_depth": order.frame_depth if order else None,
        "order_src_token": order.src_token if order else None,
        "order_dst_token": order.dst_token if order else None,
        "order_amount_in": hugeint_str(order.amount_in) if order else None,
        "user_min_amount_out": hugeint_str(order.min_amount_out) if order else None,
        "user_max_amount_in": hugeint_str(order.max_amount_in) if order else None,
        "aggregator_quoted_out": hugeint_str(order.quoted_amount_out) if order else None,
        "order_recipient": order.recipient if order else None,
        # Some routers pass uint256 max as "no deadline"
        "order_deadline": order.deadline if order and order.deadline is not None and order.deadline < 2**62 else None,
        "client_data": order.client_data if order else None,
        "exact_output": order.exact_output if order else None,
        "router_returned_out": hugeint_str(order.returned_amount_out) if order else None,
    }


def trace_trade_tx(web3: Web3, tx_hash: str) -> tuple[str, list[dict], list[dict], dict | None, str | None]:
    """Fetch the call trace of one trade transaction and decode inner Tessera calls.

    :return:
        Tuple (tx_hash, decoded call rows, order rows, outer calldata row, error message)
    """
    resp = web3.provider.make_request("debug_traceTransaction", [tx_hash, {"tracer": "callTracer", "tracerConfig": {"onlyTopCall": False}}])
    if "error" in resp:
        return tx_hash, [], [], None, str(resp["error"])

    root = resp["result"]
    root_input = root.get("input") or "0x"
    outer = {
        "tx_hash": tx_hash,
        "tx_to": Web3.to_checksum_address(root["to"]) if root.get("to") else None,
        "selector": root_input[:10] if len(root_input) >= 10 else None,
        "input": root_input,
        "input_length": (len(root_input) - 2) // 2,
    }

    rows: list[dict] = []
    order_rows: list[dict] = []
    tx_from = root.get("from")
    tx_to = root.get("to")

    def walk(frame: dict, path: list[dict]) -> None:
        path = path + [frame]
        if (frame.get("to") or "").lower() == TESSERA_SWAP.lower():
            row = decode_inner_call(frame, len(path) - 1, len(rows))
            if row is not None:
                row["tx_hash"] = tx_hash
                rows.append(row)
                order_rows.append(identify_order(tx_hash, row["call_ordinal"], path, tx_from, tx_to))
        for child in frame.get("calls", []) or []:
            walk(child, path)

    walk(root, [])
    return tx_hash, rows, order_rows, outer, None


def enrich_traces(con: duckdb.DuckDBPyConnection, web3: Web3, max_workers: int, store_tx_input: bool, start_block: int, duckdb_path: Path, min_free_disk_gb: float, checkpoint_every_chunks: int) -> None:
    """Populate trade_calls and trade_tx_inputs for every Tessera trade transaction not traced yet."""
    pending = [
        row[0]
        for row in con.execute(
            """
            SELECT DISTINCT t.tx_hash
            FROM trades t
            LEFT JOIN trade_trace_state s ON s.tx_hash = t.tx_hash
            WHERE t.venue = 'tessera' AND s.tx_hash IS NULL AND t.block_number >= ?
            """,
            [start_block],
        ).fetchall()
    ]
    logger.info("Tracing %d Tessera trade transactions with %d workers", len(pending), max_workers)
    if not pending:
        return

    batch_size = 200
    call_columns = ["tx_hash", "call_ordinal", "call_depth", "caller", "selector", "token_in", "token_out", "amount_specified", "exact_input", "amount_check", "recipient", "swap_data", "callback_data", "reverted"]
    order_columns = [
        "tx_hash", "call_ordinal", "aggregator", "frontend", "wallet_kind", "path_labels", "path_depth", "router", "router_function",
        "router_frame_depth", "order_src_token", "order_dst_token", "order_amount_in", "user_min_amount_out", "user_max_amount_in",
        "aggregator_quoted_out", "order_recipient", "order_deadline", "client_data", "exact_output", "router_returned_out",
    ]  # fmt: skip
    chunks_since_checkpoint = 0
    with tqdm(total=len(pending), desc="debug_traceTransaction", unit="tx") as progress:
        for i in range(0, len(pending), batch_size):
            ensure_disk_space(duckdb_path, min_free_disk_gb)
            batch = pending[i : i + batch_size]
            results = Parallel(n_jobs=max_workers, backend="threading")(delayed(trace_trade_tx)(web3, h) for h in batch)
            call_rows = [r for _, rows, _, _, _ in results for r in rows]
            order_rows = [r for _, _, orders, _, _ in results for r in orders]
            outer_rows = [outer for _, _, _, outer, _ in results if outer is not None]
            state_rows = [{"tx_hash": h, "traced_at": native_datetime_utc_now(), "error": err} for h, _, _, _, err in results]
            errors = sum(1 for _, _, _, _, err in results if err)
            if errors:
                logger.warning("%d of %d traces failed in batch, e.g. %s", errors, len(batch), next(err for _, _, _, _, err in results if err))
            con.begin()
            insert_frame(con, "trade_calls", pd.DataFrame(call_rows), call_columns)
            insert_frame(con, "trade_orders", pd.DataFrame(order_rows), order_columns)
            if store_tx_input:
                insert_frame(con, "trade_tx_inputs", pd.DataFrame(outer_rows), ["tx_hash", "tx_to", "selector", "input", "input_length"])
            insert_frame(con, "trade_trace_state", pd.DataFrame(state_rows), ["tx_hash", "traced_at", "error"])
            con.commit()
            progress.update(len(batch))
            chunks_since_checkpoint = checkpoint_if_due(con, duckdb_path, chunks_since_checkpoint, checkpoint_every_chunks)


def encode_view_call(token_in: str, token_out: str, amount_specified: int) -> bytes:
    """ABI-encode tesseraSwapViewAmounts(tokenIn, tokenOut, amountSpecified)."""
    return SELECTOR_VIEW_AMOUNTS + eth_abi.encode(["address", "address", "int256"], [token_in, token_out, amount_specified])


def quote_block(
    multicall: Contract,
    block_number: int,
    trades: list[tuple[str, int, str, str, int]],
    probe_fraction: float,
) -> tuple[list[dict], str | None]:
    """Quote every trade of one block at ``block_number - 1`` and ``block_number``.

    Quotes are always requested as exact-input for the observed ``amount_in``,
    even for exact-output trades, so the metric is comparable across trades.
    ``msg.sender`` seen by the engine is the Multicall3 contract.

    :param trades:
        Tuples (tx_hash, log_index, token_in, token_out, amount_in)

    :return:
        Tuple (quote rows, error message)
    """
    plan: list[tuple[str, int, str, int]] = []
    calls: list[tuple[str, bytes]] = []
    for tx_hash, log_index, token_in, token_out, amount_in in trades:
        probe = max(1, int(amount_in * probe_fraction))
        for size, amount in (("trade", amount_in), ("probe", probe)):
            plan.append((tx_hash, log_index, size, amount))
            calls.append((TESSERA_SWAP, encode_view_call(token_in, token_out, amount)))

    rows: list[dict] = []
    for kind, block in (("prev", block_number - 1), ("same", block_number)):
        try:
            results = multicall.functions.tryAggregate(False, calls).call(block_identifier=block)
        except Exception as e:  # noqa: BLE001 - provider errors are heterogeneous, recorded per block
            return rows, f"block {block}: {type(e).__name__}: {str(e)[:200]}"
        for (tx_hash, log_index, size, amount), (success, output) in zip(plan, results):
            quoted_in = quoted_out = None
            if success and len(output) >= 64:
                quoted_in, quoted_out = eth_abi.decode(["uint256", "uint256"], output)
            rows.append(
                {
                    "tx_hash": tx_hash,
                    "log_index": log_index,
                    "quote_block": block,
                    "kind": kind,
                    "size": size,
                    "amount_specified": str(amount),
                    "quoted_amount_in": hugeint_str(quoted_in),
                    "quoted_amount_out": hugeint_str(quoted_out),
                    "success": bool(success and quoted_out is not None),
                }
            )
    return rows, None


def enrich_quotes(con: duckdb.DuckDBPyConnection, web3: Web3, probe_fraction: float, max_workers: int, start_block: int, duckdb_path: Path, min_free_disk_gb: float, checkpoint_every_chunks: int) -> None:
    """Populate quotes for every Tessera trade that has none yet."""
    pending = con.execute(
        """
        SELECT t.block_number, t.tx_hash, t.log_index, t.token_in, t.token_out, CAST(t.amount_in AS VARCHAR)
        FROM trades t
        LEFT JOIN quotes q ON q.tx_hash = t.tx_hash AND q.log_index = t.log_index
        WHERE t.venue = 'tessera' AND q.tx_hash IS NULL AND t.amount_in IS NOT NULL AND t.block_number >= ?
        ORDER BY t.block_number
        """,
        [start_block],
    ).fetchall()
    by_block: dict[int, list[tuple[str, int, str, str, int]]] = {}
    for block_number, tx_hash, log_index, token_in, token_out, amount_in in pending:
        by_block.setdefault(int(block_number), []).append((tx_hash, int(log_index), token_in, token_out, int(amount_in)))
    logger.info("Quoting %d Tessera trades across %d blocks with %d workers", len(pending), len(by_block), max_workers)
    if not by_block:
        return

    multicall = get_multicall_contract(web3)
    blocks = sorted(by_block)
    batch_size = 100
    quote_columns = ["tx_hash", "log_index", "quote_block", "kind", "size", "amount_specified", "quoted_amount_in", "quoted_amount_out", "success"]
    chunks_since_checkpoint = 0
    with tqdm(total=len(blocks), desc="historical quotes", unit="block") as progress:
        for i in range(0, len(blocks), batch_size):
            ensure_disk_space(duckdb_path, min_free_disk_gb)
            batch = blocks[i : i + batch_size]
            results = Parallel(n_jobs=max_workers, backend="threading")(delayed(quote_block)(multicall, b, by_block[b], probe_fraction) for b in batch)
            rows = [r for quote_rows, _ in results for r in quote_rows]
            errors = [err for _, err in results if err]
            if errors:
                logger.warning("%d of %d quote blocks failed, e.g. %s", len(errors), len(batch), errors[0])
            con.begin()
            insert_frame(con, "quotes", pd.DataFrame(rows), quote_columns)
            con.commit()
            progress.update(len(batch))
            chunks_since_checkpoint = checkpoint_if_due(con, duckdb_path, chunks_since_checkpoint, checkpoint_every_chunks)


def refresh_tokens(con: duckdb.DuckDBPyConnection, web3: Web3) -> None:
    """Fetch symbol and decimals for tokens not yet in the tokens table."""
    missing = [
        row[0]
        for row in con.execute("""
            SELECT DISTINCT address FROM (
                SELECT token_in AS address FROM trades
                UNION
                SELECT token_out AS address FROM trades
            )
            WHERE address NOT IN (SELECT address FROM tokens)
        """).fetchall()
    ]
    if not missing:
        return
    logger.info("Fetching details for %d new tokens", len(missing))
    rows = []
    for address in missing:
        details = fetch_erc20_details(web3, address, raise_on_error=False)
        rows.append({"address": address, "symbol": details.symbol, "decimals": details.decimals})
    con.begin()
    insert_frame(con, "tokens", pd.DataFrame(rows), ["address", "symbol", "decimals"])
    con.commit()


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def resolve_config(con: duckdb.DuckDBPyConnection, client: ThrottledHypersyncClient, duckdb_path: Path) -> ScanConfig:
    """Build the run configuration from environment variables and saved state."""
    tip = asyncio.run(client.get_height())
    tip_safety = int(os.environ.get("TIP_SAFETY_BLOCKS", "10"))
    end_block = int(os.environ.get("END_BLOCK", tip - tip_safety))

    if "START_BLOCK" in os.environ:
        start_block = int(os.environ["START_BLOCK"])
    else:
        last = read_scan_state(con, "last_scanned_block")
        if last is not None:
            start_block = last + 1
        else:
            start_block = end_block - int(os.environ.get("LOOKBACK_BLOCKS", "43200"))
    start_block = max(start_block, TESSERA_DEPLOY_BLOCK)

    return ScanConfig(
        duckdb_path=duckdb_path,
        start_block=start_block,
        end_block=end_block,
        chunk_blocks=int(os.environ.get("CHUNK_BLOCKS", "10000")),
        checkpoint_every_chunks=int(os.environ.get("CHECKPOINT_EVERY_CHUNKS", "20")),
        min_free_disk_gb=float(os.environ.get("MIN_FREE_DISK_GB", "10")),
        scan_block_tx_counts=env_bool("SCAN_BLOCK_TX_COUNTS", False),
        enrich_traces=env_bool("ENRICH_TRACES", True),
        store_tx_input=env_bool("STORE_TX_INPUT", True),
        enrich_quotes=env_bool("ENRICH_QUOTES", True),
        probe_fraction=float(os.environ.get("PROBE_FRACTION", "0.01")),
        max_workers=int(os.environ.get("MAX_WORKERS", "8")),
        enrich_start_block=int(os.environ.get("ENRICH_START_BLOCK", "0")),
        recv_timeout=float(os.environ.get("HYPERSYNC_RECV_TIMEOUT", "120")),
    )


def ensure_disk_space(duckdb_path: Path, min_free_gb: float) -> None:
    """Abort loudly before a chunk if the DuckDB volume is close to full.

    A silent out-of-disk mid-write is worse than a clear early failure: the
    scan is resumable, so failing fast just means rerunning the same command
    once space is freed.
    """
    free_gb = shutil.disk_usage(duckdb_path.parent).free / 1e9
    if free_gb < min_free_gb:
        raise RuntimeError(f"Only {free_gb:.1f} GB free on {duckdb_path.parent}, below MIN_FREE_DISK_GB={min_free_gb}. Free up space or lower MIN_FREE_DISK_GB, then rerun -- the scan resumes from the last committed block.")
    if free_gb < min_free_gb * 2:
        logger.warning("Free disk space is getting low: %.1f GB left on %s", free_gb, duckdb_path.parent)


def checkpoint_if_due(con: duckdb.DuckDBPyConnection, duckdb_path: Path, chunks_since_checkpoint: int, checkpoint_every_chunks: int) -> int:
    """Run an explicit CHECKPOINT every ``checkpoint_every_chunks`` committed chunks.

    This flushes the WAL into the main file with DuckDB's automatic per-column
    compression and truncates the WAL, so a multi-day backfill keeps a bounded,
    mostly-compressed footprint instead of one giant WAL until the final close.
    Always called between committed chunks, never inside a transaction.

    :return:
        Updated ``chunks_since_checkpoint`` counter.
    """
    if checkpoint_every_chunks <= 0:
        return chunks_since_checkpoint
    chunks_since_checkpoint += 1
    if chunks_since_checkpoint < checkpoint_every_chunks:
        return chunks_since_checkpoint

    started = time.time()
    con.execute("CHECKPOINT")
    main_mb = duckdb_path.stat().st_size / 1e6 if duckdb_path.exists() else 0
    wal_path = duckdb_path.with_suffix(duckdb_path.suffix + ".wal")
    wal_mb = wal_path.stat().st_size / 1e6 if wal_path.exists() else 0
    logger.info("Checkpointed in %.1f s: main file %.0f MB, WAL %.0f MB", time.time() - started, main_mb, wal_mb)
    return 0


def log_summary(con: duckdb.DuckDBPyConnection, duckdb_path: Path) -> None:
    """Log row counts and on-disk size so the operator can see the database grew."""
    for table in ("blocks", "block_tx_counts", "trades", "price_updates", "trade_calls", "trade_orders", "trade_tx_inputs", "quotes", "tokens"):
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        logger.info("Table %s: %d rows", table, count)
    venues = con.execute("SELECT venue, COUNT(*), MIN(block_number), MAX(block_number) FROM trades GROUP BY venue").fetchall()
    for venue, count, lo, hi in venues:
        logger.info("Venue %s: %d trades, blocks %s - %s", venue, count, f"{lo:,}", f"{hi:,}")
    main_mb = duckdb_path.stat().st_size / 1e6 if duckdb_path.exists() else 0
    wal_path = duckdb_path.with_suffix(duckdb_path.suffix + ".wal")
    wal_mb = wal_path.stat().st_size / 1e6 if wal_path.exists() else 0
    free_gb = shutil.disk_usage(duckdb_path.parent).free / 1e9
    logger.info("On disk: main file %.0f MB (compressed), uncheckpointed WAL %.0f MB, %.1f GB free on volume", main_mb, wal_mb, free_gb)


def main() -> None:
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    json_rpc_base = os.environ.get("JSON_RPC_BASE")
    assert json_rpc_base, "JSON_RPC_BASE must be set"
    web3 = create_multi_provider_web3(json_rpc_base)
    assert web3.eth.chain_id == CHAIN_ID, f"Expected Base, got chain {web3.eth.chain_id}"

    hypersync_config = configure_hypersync_from_env(web3)
    client = hypersync_config.hypersync_client
    assert client is not None, "Hypersync client could not be configured, check HYPERSYNC_API_KEY"

    duckdb_path = Path(os.environ.get("TESSERA_DUCKDB_PATH", "~/.tradingstrategy/tessera/tessera-base.duckdb")).expanduser()
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(duckdb_path))
    try:
        create_schema(con)
        config = resolve_config(con, client, duckdb_path)
        logger.info("Scanning Tessera on Base, blocks %s - %s (%d blocks) into %s", f"{config.start_block:,}", f"{config.end_block:,}", config.end_block - config.start_block + 1, duckdb_path)

        if config.end_block >= config.start_block:
            chunk_starts = range(config.start_block, config.end_block + 1, config.chunk_blocks)
            total_trades = total_updates = 0
            chunks_since_checkpoint = 0
            chunk_progress = tqdm(chunk_starts, desc="Tessera activity scan", unit="chunk")
            for chunk_start in chunk_progress:
                ensure_disk_space(duckdb_path, config.min_free_disk_gb)
                chunk_end = min(chunk_start + config.chunk_blocks - 1, config.end_block)
                started = time.time()
                trades, updates = scan_activity_chunk(con, client, chunk_start, chunk_end, config.scan_block_tx_counts, config.recv_timeout)
                total_trades += trades
                total_updates += updates
                chunk_progress.set_postfix({"block": f"{chunk_end:,}", "trades": f"{total_trades:,}", "updates": f"{total_updates:,}"})
                logger.info("Blocks %s - %s: %d trades, %d price updates in %.1f s", f"{chunk_start:,}", f"{chunk_end:,}", trades, updates, time.time() - started)
                chunks_since_checkpoint = checkpoint_if_due(con, duckdb_path, chunks_since_checkpoint, config.checkpoint_every_chunks)
            logger.info("Activity scan done: %d trades, %d price updates", total_trades, total_updates)
        else:
            logger.info("Nothing new to scan, last scanned block is %s", f"{config.start_block - 1:,}")

        refresh_tokens(con, web3)

        if config.enrich_traces:
            enrich_traces(con, web3, config.max_workers, config.store_tx_input, config.enrich_start_block, duckdb_path, config.min_free_disk_gb, config.checkpoint_every_chunks)

        if config.enrich_quotes:
            enrich_quotes(con, web3, config.probe_fraction, config.max_workers, config.enrich_start_block, duckdb_path, config.min_free_disk_gb, config.checkpoint_every_chunks)

        log_summary(con, duckdb_path)
    finally:
        con.close()


if __name__ == "__main__":
    main()

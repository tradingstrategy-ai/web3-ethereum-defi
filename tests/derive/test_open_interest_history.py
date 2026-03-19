"""Test Derive perp snapshot on-chain fetch and DuckDB storage.

Tests the on-chain approach to historical open interest, perp price, and
index price data via Multicall3-batched view function calls on Derive Chain
perp contracts, and DuckDB snapshot persistence via
:py:class:`DeriveFundingRateDatabase`.

Requires a live connection to the Derive Chain RPC (https://rpc.derive.xyz)
and the Derive REST API.  No credentials required.
"""

import datetime
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.derive.api import (
    fetch_instrument_details,
    fetch_open_interest_onchain,
    fetch_perp_snapshots_multicall,
)
from eth_defi.derive.constants import DERIVE_MAINNET_RPC_URL
from eth_defi.derive.historical import DeriveFundingRateDatabase, estimate_block_at_timestamp
from eth_defi.derive.session import create_derive_session

#: ETH-PERP contract address on Derive Mainnet
ETH_PERP_CONTRACT = "0xAf65752C4643E25C02F693f9D4FE19cF23a095E3"


@pytest.fixture(scope="module")
def session():
    """Create a shared HTTP session for all tests in this module."""
    return create_derive_session()


@pytest.fixture(scope="module")
def w3():
    """Create a shared Web3 connection to Derive Chain."""
    return Web3(Web3.HTTPProvider(DERIVE_MAINNET_RPC_URL))


@pytest.mark.timeout(60)
def test_fetch_open_interest_onchain_current(w3: Web3):
    """Fetch the current ETH-PERP open interest from the on-chain contract.

    Verifies that the ``openInterest(uint256)`` view function on the Derive
    Chain returns a positive value at the latest block.

    1. Get the latest block number.
    2. Call openInterest(0) at that block.
    3. Assert the result is a positive Decimal.
    """
    # 1. Get latest block
    latest = w3.eth.block_number
    assert latest > 0

    # 2. Fetch on-chain OI at latest block
    oi = fetch_open_interest_onchain(w3, ETH_PERP_CONTRACT, block_number=latest)

    # 3. Assert positive result
    assert oi is not None, "Expected non-None OI at latest block for ETH-PERP"
    assert isinstance(oi, Decimal)
    assert oi > 0, f"Expected positive OI, got {oi}"
    assert oi > 100, f"Suspiciously low OI: {oi}"
    assert oi < 1_000_000, f"Suspiciously high OI: {oi}"


@pytest.mark.timeout(60)
def test_fetch_perp_snapshots_multicall(w3: Web3):
    """Fetch OI, perp price, and index price via Multicall3 for ETH-PERP.

    Verifies that the multicall returns all three data points with
    sensible values at the current block.

    1. Get the latest block number.
    2. Call fetch_perp_snapshots_multicall for ETH-PERP.
    3. Assert open_interest is positive and in a sane range.
    4. Assert perp_price is a positive USD value in a sane range.
    5. Assert index_price is a positive USD value in a sane range.
    6. Assert perp_price and index_price are close to each other.
    """
    # 1. Get latest block
    latest = w3.eth.block_number
    assert latest > 0

    # 2. Multicall snapshot
    results = fetch_perp_snapshots_multicall(w3, [ETH_PERP_CONTRACT], latest)
    assert len(results) == 1
    snap = results[0]

    # 3. Open interest
    assert snap.open_interest is not None, "Expected non-None OI"
    assert isinstance(snap.open_interest, Decimal)
    assert snap.open_interest > 100, f"Suspiciously low OI: {snap.open_interest}"
    assert snap.open_interest < 1_000_000, f"Suspiciously high OI: {snap.open_interest}"

    # 4. Perp price (mark price) — ETH should be $100–$100,000
    assert snap.perp_price is not None, "Expected non-None perp_price"
    assert isinstance(snap.perp_price, Decimal)
    assert snap.perp_price > 100, f"Suspiciously low perp_price: {snap.perp_price}"
    assert snap.perp_price < 100_000, f"Suspiciously high perp_price: {snap.perp_price}"

    # 5. Index price (spot price) — same range
    assert snap.index_price is not None, "Expected non-None index_price"
    assert isinstance(snap.index_price, Decimal)
    assert snap.index_price > 100, f"Suspiciously low index_price: {snap.index_price}"
    assert snap.index_price < 100_000, f"Suspiciously high index_price: {snap.index_price}"

    # 6. Mark and index prices should be within 5% of each other
    ratio = float(snap.perp_price / snap.index_price)
    assert 0.95 < ratio < 1.05, f"perp_price/index_price ratio out of range: {ratio}"


@pytest.mark.timeout(60)
def test_fetch_perp_snapshots_multicall_historical(w3: Web3):
    """Fetch perp snapshots at a historical block 30 days ago.

    Verifies that all three data points are available historically and
    differ from current values.

    1. Estimate the block number 30 days ago.
    2. Fetch snapshots at current and historical blocks.
    3. Assert all data points are positive at both blocks.
    4. Assert OI values differ between current and 30 days ago.
    """
    # 1. Estimate block 30 days ago
    latest_blk = w3.eth.get_block("latest")
    target_ts = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).timestamp())
    historical_block = estimate_block_at_timestamp(
        w3,
        target_ts,
        latest_block=latest_blk.number,
        latest_ts=latest_blk.timestamp,
    )
    assert historical_block > 0

    # 2. Fetch snapshots at both blocks
    snaps_now = fetch_perp_snapshots_multicall(w3, [ETH_PERP_CONTRACT], latest_blk.number)
    snaps_30d = fetch_perp_snapshots_multicall(w3, [ETH_PERP_CONTRACT], historical_block)

    now = snaps_now[0]
    hist = snaps_30d[0]

    # 3. All data points should be positive at both blocks
    assert now.open_interest is not None and now.open_interest > 0
    assert now.perp_price is not None and now.perp_price > 0
    assert now.index_price is not None and now.index_price > 0
    assert hist.open_interest is not None and hist.open_interest > 0
    assert hist.perp_price is not None and hist.perp_price > 0
    assert hist.index_price is not None and hist.index_price > 0

    # 4. OI should differ between current and 30 days ago
    assert now.open_interest != hist.open_interest, "Expected different OI at current vs 30 days ago"


@pytest.mark.timeout(120)
def test_open_interest_db_backfill_and_resume(session, w3: Web3, tmp_path):
    """Backfill 3 days of ETH-PERP snapshots into DuckDB and verify all data points.

    Tests the full on-chain backfill pipeline: historical fetch with
    Multicall3 batching, storage of OI + prices, idempotent resume.

    1. Sync last 3 days of snapshots for ETH-PERP.
    2. Assert rows were inserted.
    3. Re-sync the same window — assert 0 new rows (idempotent).
    4. Assert DataFrame has correct columns including perp_price and index_price.
    5. Assert all OI values are positive and all price values are present and positive.
    6. Assert sync state records oldest/newest timestamps.
    """
    db = DeriveFundingRateDatabase(tmp_path / "funding-rates.duckdb")
    try:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        start = (now - datetime.timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (now - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        # 1. First sync: 3 days
        inserted = db.sync_open_interest_instrument(
            session,
            "ETH-PERP",
            w3=w3,
            start_time=start,
            end_time=end,
        )

        # 2. Should have inserted rows (3 days = 3 rows if all have non-zero OI)
        assert inserted >= 2, f"Expected at least 2 new rows, got {inserted}"
        count = db.get_open_interest_row_count("ETH-PERP")
        assert count == inserted

        # 3. Second sync of same window — should insert 0 new rows
        inserted_again = db.sync_open_interest_instrument(
            session,
            "ETH-PERP",
            w3=w3,
            start_time=start,
            end_time=end,
        )
        assert inserted_again == 0, f"Expected 0 new rows on re-sync, got {inserted_again}"
        assert db.get_open_interest_row_count("ETH-PERP") == count

        # 4. DataFrame shape and columns
        df = db.get_open_interest_dataframe("ETH-PERP")
        assert len(df) == count
        assert "timestamp" in df.columns
        assert "open_interest" in df.columns
        assert "instrument" in df.columns
        assert "perp_price" in df.columns
        assert "index_price" in df.columns

        # 5. All data points should be positive
        assert (df["open_interest"] > 0).all(), "All OI values should be positive"
        assert df["perp_price"].notna().all(), "All perp_price values should be present"
        assert (df["perp_price"] > 0).all(), "All perp_price values should be positive"
        assert df["index_price"].notna().all(), "All index_price values should be present"
        assert (df["index_price"] > 0).all(), "All index_price values should be positive"

        # Sanity: prices should be in a reasonable range for ETH ($100–$100,000)
        assert (df["perp_price"] > 100).all(), "perp_price below $100"
        assert (df["perp_price"] < 100_000).all(), "perp_price above $100,000"
        assert (df["index_price"] > 100).all(), "index_price below $100"
        assert (df["index_price"] < 100_000).all(), "index_price above $100,000"

        # 6. Sync state
        state = db.get_open_interest_sync_state("ETH-PERP")
        assert state is not None
        assert state["row_count"] == count
        assert state["oldest_ts"] > 0
        assert state["newest_ts"] >= state["oldest_ts"]

    finally:
        db.close()


@pytest.mark.timeout(60)
def test_fetch_instrument_details(session):
    """Fetch instrument details including on-chain contract addresses.

    Verifies that fetch_instrument_details() returns the base_asset_address
    and scheduled_activation for known instruments.

    1. Fetch all instrument details.
    2. Assert ETH-PERP is present with correct contract address.
    3. Assert scheduled_activation is a valid Unix timestamp.
    """
    # 1. Fetch details
    details = fetch_instrument_details(session)

    # 2. Check ETH-PERP
    assert "ETH-PERP" in details, "ETH-PERP should be in active instruments"
    eth = details["ETH-PERP"]
    assert eth["base_asset_address"].lower() == ETH_PERP_CONTRACT.lower()

    # 3. Activation timestamp should be in 2023
    activation = datetime.datetime.fromtimestamp(eth["scheduled_activation"], tz=datetime.timezone.utc)
    assert activation.year == 2023, f"Unexpected activation year: {activation}"

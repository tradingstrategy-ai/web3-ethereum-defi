"""Test Derive open interest on-chain fetch and DuckDB storage.

Tests the on-chain approach to historical open interest data via the
``openInterest(uint256)`` view function on Derive Chain perp contracts,
and DuckDB snapshot persistence via :py:class:`DeriveFundingRateDatabase`.

Requires a live connection to the Derive Chain RPC (https://rpc.derive.xyz)
and the Derive REST API.  No credentials required.
"""

import datetime
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.derive.api import fetch_instrument_details, fetch_open_interest_onchain
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
    # Sanity check: ETH-PERP OI should be in the hundreds to tens of thousands range
    assert oi > 100, f"Suspiciously low OI: {oi}"
    assert oi < 1_000_000, f"Suspiciously high OI: {oi}"


@pytest.mark.timeout(60)
def test_fetch_open_interest_onchain_historical(w3: Web3):
    """Fetch ETH-PERP open interest at a historical block 30 days ago.

    Verifies that the Derive Chain archive node supports historical eth_call
    and that OI values differ between current and historical blocks.

    1. Estimate the block number 30 days ago.
    2. Fetch OI at current block and historical block.
    3. Assert both are positive and are different values.
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

    # 2. Fetch OI at both blocks
    oi_now = fetch_open_interest_onchain(w3, ETH_PERP_CONTRACT, block_number=latest_blk.number)
    oi_30d = fetch_open_interest_onchain(w3, ETH_PERP_CONTRACT, block_number=historical_block)

    # 3. Both should be positive and different (OI changes over time)
    assert oi_now is not None and oi_now > 0
    assert oi_30d is not None and oi_30d > 0
    assert oi_now != oi_30d, "Expected different OI at current vs 30 days ago"


@pytest.mark.timeout(120)
def test_open_interest_db_backfill_and_resume(session, w3: Web3, tmp_path):
    """Backfill 3 days of ETH-PERP OI into DuckDB and verify resume inserts 0 rows.

    Tests the full on-chain backfill pipeline: historical fetch, storage,
    idempotent resume.

    1. Sync last 3 days of OI for ETH-PERP.
    2. Assert 3 rows were inserted.
    3. Re-sync the same window — assert 0 new rows (idempotent).
    4. Assert DataFrame has correct columns and positive OI values.
    5. Assert sync state records oldest/newest timestamps.
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
        assert (df["open_interest"] > 0).all(), "All OI values should be positive"

        # 5. Sync state
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

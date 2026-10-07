"""Test Derive perp snapshot onchain fetch and DuckDB storage.

Tests the onchain approach to historical open interest, perp price, and
index price data via Multicall3-batched view function calls on Derive Chain
perp contracts, and DuckDB snapshot persistence via
:py:class:`DeriveFundingRateDatabase`.

Requires a live connection to the Derive Chain RPC (https://rpc.derive.xyz)
and the Derive REST API.  No credentials required.

Price-dependent tests use a verified historical window because even the
current oracle can refuse stale prices. Live open interest is tested
separately, and a fixed stale-oracle block checks that price refusals remain
``None`` rather than invented values.
"""

import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from eth_typing import HexAddress
from requests import Session
from web3 import Web3
from web3.exceptions import ContractCustomError

from eth_defi.derive.api import (
    PERP_GET_INDEX_PRICE_SELECTOR,
    PERP_GET_PERP_PRICE_SELECTOR,
    fetch_instrument_details,
    fetch_open_interest_onchain,
    fetch_perp_snapshots_multicall,
)
from eth_defi.derive.constants import DERIVE_MAINNET_RPC_URL
from eth_defi.derive.historical import DeriveFundingRateDatabase, estimate_block_at_timestamp
from eth_defi.derive.session import create_derive_session

#: ETH-PERP contract address on Derive mainnet.
ETH_PERP_CONTRACT = HexAddress("0xAf65752C4643E25C02F693f9D4FE19cF23a095E3")

#: Priced state from 2026-10-06, verified on 2026-10-07.
#: On 2026-10-07, all three price-dependent tests failed both locally and in
#: CI because current direct price calls reverted with ``BLF_DataTooOld()``.
ETH_PERP_PRICED_BLOCK = 45_629_261

#: Historical oracle heartbeat expiry documented in the earlier CI repair.
ETH_PERP_STALE_BLOCK = 44_428_993


@pytest.fixture(scope="module")
def session() -> Session:
    """Create a shared HTTP session for all tests in this module."""
    return create_derive_session()


@pytest.fixture(scope="module")
def w3() -> Web3:
    """Create a shared Web3 connection to Derive Chain."""
    return Web3(Web3.HTTPProvider(DERIVE_MAINNET_RPC_URL))


@pytest.mark.timeout(60)
def test_fetch_open_interest_onchain_current(w3: Web3) -> None:
    """Fetch the current ETH-PERP open interest from the onchain contract.

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
def test_fetch_perp_snapshots_multicall(w3: Web3) -> None:
    """Fetch OI, perp price, and index price via Multicall3 for ETH-PERP.

    Verifies that the multicall returns all three data points with
    exact values at a fixed block with a healthy oracle.

    1. Use the verified historical block number.
    2. Call fetch_perp_snapshots_multicall for ETH-PERP.
    3. Assert the exact open interest.
    4. Assert the exact perp price.
    5. Assert the exact index price.
    6. Assert perp_price and index_price are close to each other.
    """
    # 2. Multicall snapshot
    results = fetch_perp_snapshots_multicall(w3, [ETH_PERP_CONTRACT], ETH_PERP_PRICED_BLOCK)
    assert len(results) == 1
    snap = results[0]

    # 3. Open interest
    assert snap.open_interest is not None, "Expected non-None OI"
    assert isinstance(snap.open_interest, Decimal)
    assert snap.open_interest == Decimal("14728.347742960263774203")

    # 4. Perp price (mark price)
    assert snap.perp_price is not None, "Expected non-None perp_price"
    assert isinstance(snap.perp_price, Decimal)
    assert snap.perp_price == Decimal("2704.1927870709508691")

    # 5. Index price (spot price)
    assert snap.index_price is not None, "Expected non-None index_price"
    assert isinstance(snap.index_price, Decimal)
    assert snap.index_price == Decimal("2705.6377239739713")

    # 6. Mark and index prices should be within 5% of each other
    ratio = float(snap.perp_price / snap.index_price)
    assert 0.95 < ratio < 1.05, f"perp_price/index_price ratio out of range: {ratio}"


@pytest.mark.timeout(60)
def test_fetch_perp_snapshots_multicall_historical(w3: Web3) -> None:
    """Fetch perp snapshots at fixed historical blocks 30 days apart.

    Verifies that all three data points are available historically and
    differ between the verified reference block and its earlier state.

    1. Estimate the block number 30 days before the reference block.
    2. Fetch snapshots at reference and historical blocks.
    3. Assert exact OI and price values at the earlier block.
    4. Assert OI values differ between the blocks.
    """
    # 1. Estimate the block 30 days before the reference block.
    latest_blk = w3.eth.get_block(ETH_PERP_PRICED_BLOCK)
    target_ts = latest_blk.timestamp - int(datetime.timedelta(days=30).total_seconds())
    historical_block = estimate_block_at_timestamp(
        w3,
        target_ts,
        latest_block=latest_blk.number,
        latest_ts=latest_blk.timestamp,
    )
    assert historical_block > 0

    # 2. Fetch snapshots at both blocks.
    snaps_now = fetch_perp_snapshots_multicall(w3, [ETH_PERP_CONTRACT], latest_blk.number)
    snaps_30d = fetch_perp_snapshots_multicall(w3, [ETH_PERP_CONTRACT], historical_block)

    reference = snaps_now[0]
    hist = snaps_30d[0]

    # 3. All data points should be positive at both blocks
    assert reference.open_interest == Decimal("14728.347742960263774203")
    assert reference.perp_price == Decimal("2704.1927870709508691")
    assert reference.index_price == Decimal("2705.6377239739713")
    assert hist.open_interest == Decimal("6526.307751846037008412")
    assert hist.perp_price == Decimal("2496.1615516619772654")
    assert hist.index_price == Decimal("2497.0633439613457")

    # 4. OI should differ between the two historical states.
    assert reference.open_interest != hist.open_interest


@pytest.mark.timeout(120)
def test_open_interest_db_backfill_and_resume(session: Session, w3: Web3, tmp_path: Path) -> None:
    """Backfill a short hourly window of ETH-PERP snapshots into DuckDB and verify all data points.

    Tests the full onchain backfill pipeline at a verified historical window:
    Multicall3 batching, storage of OI + prices, and idempotent resume.

    1. Sync a short hourly window of snapshots for ETH-PERP.
    2. Assert rows were inserted.
    3. Re-sync the same window — assert 0 new rows (idempotent).
    4. Assert DataFrame has correct columns including perp_price and index_price.
    5. Assert all OI values are positive and validate the available price samples.
    6. Assert sync state records oldest/newest timestamps.
    """
    db = DeriveFundingRateDatabase(tmp_path / "funding-rates.duckdb")
    try:
        reference = w3.eth.get_block(ETH_PERP_PRICED_BLOCK)
        reference_time = datetime.datetime.fromtimestamp(reference.timestamp, datetime.UTC).replace(tzinfo=None, minute=0, second=0, microsecond=0)
        end = reference_time - datetime.timedelta(hours=1)
        start = end - datetime.timedelta(hours=6)

        # 1. First sync: 6 hours
        inserted = db.sync_open_interest_instrument(
            session,
            "ETH-PERP",
            w3=w3,
            start_time=start,
            end_time=end,
        )

        # 2. Should have inserted rows
        assert inserted >= 4, f"Expected at least 4 new rows, got {inserted}"
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

        # 5. OI is available independently of Derive's price oracle and must
        # remain positive even when the oracle deliberately refuses stale data.
        assert (df["open_interest"] > 0).all(), "All OI values should be positive"

        # This fixed window was verified to have a healthy oracle on every
        # sample. Require all prices here; the separate stale-oracle test
        # exercises valid ``None`` results without weakening this coverage.
        assert df["perp_price"].notna().all()
        assert df["index_price"].notna().all()
        # Sanity: prices should be in a reasonable range for ETH.
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
def test_fetch_perp_snapshots_multicall_stale_oracle(w3: Web3) -> None:
    """Preserve missing prices when the onchain oracle refuses stale data.

    At Derive block 44,428,993, open interest is available but both price
    calls revert with ``BLF_DataTooOld()``. Verify the actual direct reverts
    before asserting that the Multicall adapter returns ``None``.
    """
    address = w3.to_checksum_address(ETH_PERP_CONTRACT)
    for selector in (PERP_GET_PERP_PRICE_SELECTOR, PERP_GET_INDEX_PRICE_SELECTOR):
        with pytest.raises(ContractCustomError, match="0x1141796d"):
            w3.eth.call({"to": address, "data": "0x" + selector}, block_identifier=ETH_PERP_STALE_BLOCK)

    snapshot = fetch_perp_snapshots_multicall(w3, [ETH_PERP_CONTRACT], ETH_PERP_STALE_BLOCK)[0]
    assert snapshot.open_interest == Decimal("6625.838643026517110298")
    assert snapshot.perp_price is None
    assert snapshot.index_price is None


@pytest.mark.timeout(60)
def test_fetch_instrument_details(session: Session) -> None:
    """Fetch instrument details including onchain contract addresses.

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

"""Tests for the protocol-independent perpetual vault account contract."""

import datetime
from decimal import Decimal

import duckdb
import pandas as pd
import pyarrow as pa
import pytest

from eth_defi.hyperliquid.api import AssetPosition, MarginSummary, PerpClearinghouseState
from eth_defi.hyperliquid.perp_metrics import build_hyperliquid_vault_observation_bundle
from eth_defi.hyperliquid.vault import VaultSummary
from eth_defi.lighter.constants import LIGHTER_ETHEREUM
from eth_defi.lighter.perp_metrics import build_lighter_pool_observation_bundle
from eth_defi.pacifica.perp_metrics import PACIFICA_PERP_VAULT_METRICS_SUPPORTED, build_pacifica_lake_observation_bundle
from eth_defi.perp_dex.adapter import PerpDexCapability, PerpDexCapabilityRegistry, embed_perp_capability_registry
from eth_defi.perp_dex.export import build_perp_dex_other_data
from eth_defi.perp_dex.metrics import (
    PerpVaultAccountObservation,
    PerpVaultIdentity,
    PerpVaultObservationBundle,
    PerpVaultPositionObservation,
    PositionValuationBasis,
    SourcePositionDataStatus,
    create_unavailable_perp_vault_observation_bundle,
    derive_perp_vault_exposure,
)
from eth_defi.perp_dex.parquet import (
    PERP_METRICS_MAX_AGE,
    PERP_METRICS_MAX_FORWARD_ALIGNMENT,
    attach_perp_metrics_to_price_rows,
    derive_perp_vault_metric_snapshots,
    finalise_perp_metric_columns,
    normalise_perp_metric_parquet_dtypes,
)
from eth_defi.perp_dex.storage import (
    initialise_perp_vault_observation_schema,
    read_perp_vault_observations,
    write_perp_vault_observation_bundle,
)
from eth_defi.research.wrangle_vault_prices import forward_fill_vault
from eth_defi.vault.base import VaultHistoricalRead

EXPECTED_CORRECTED_LONG = 55.0
EXPECTED_LONG_NOTIONAL = 100.0
EXPECTED_OPEN_POSITION_COUNT = 2
TEST_PERP_CHAIN_ID = 9999


def _dt(value: str) -> datetime.datetime:
    """Create the repository's canonical naive UTC fixture timestamp."""
    return datetime.datetime.fromisoformat(value)


def _bundle(
    snapshot_id: str,
    written_at: datetime.datetime,
    notionals: tuple[str, ...] = ("100", "-40"),
    *,
    status: SourcePositionDataStatus = SourcePositionDataStatus.available,
    collector_version: str = "1.0",
    observed_at: datetime.datetime | None = None,
) -> PerpVaultObservationBundle:
    """Create a valid fixture bundle with a stable effective timestamp."""
    observed_at = observed_at or _dt("2026-07-24T12:00:00")
    identity = PerpVaultIdentity("test-perp", "mainnet", "vault-1", TEST_PERP_CHAIN_ID, "vault-1")
    account = PerpVaultAccountObservation(
        identity=identity,
        snapshot_id=snapshot_id,
        observed_at=observed_at,
        written_at=written_at,
        position_effective_at=observed_at,
        equity_effective_at=None,
        total_equity=Decimal("1000"),
        quote_asset="USDC",
        position_data_status=status,
        position_data_reason="fixture",
        position_set_complete=status is SourcePositionDataStatus.available,
        source_endpoint="fixture",
        collector_version=collector_version,
    )
    positions = tuple(
        PerpVaultPositionObservation(
            snapshot_id=snapshot_id,
            source_market_id=f"market-{idx}",
            signed_notional=Decimal(notional),
            quote_asset="USDC",
            valuation_basis=PositionValuationBasis.source_position_value,
            valuation_observed_at=observed_at,
            source_endpoint="fixture",
        )
        for idx, notional in enumerate(notionals)
    )
    return PerpVaultObservationBundle(account, positions)


def test_empty_available_bundle_is_a_real_zero_exposure() -> None:
    """A complete empty response must not become unavailable or disappear."""
    bundle = _bundle("empty", _dt("2026-07-24T12:00:00"), notionals=())

    exposure = derive_perp_vault_exposure(bundle)

    assert exposure.long_notional == 0
    assert exposure.short_notional == 0
    assert exposure.open_position_count == 0
    assert exposure.largest_position_notional == 0


def test_account_only_source_does_not_become_a_flat_portfolio() -> None:
    """Authentication and privacy gaps retain their explicit null semantics."""
    bundle = create_unavailable_perp_vault_observation_bundle(
        identity=PerpVaultIdentity("test-perp", "mainnet", "vault-1", TEST_PERP_CHAIN_ID, "vault-1"),
        observed_at=_dt("2026-07-24T12:00:00"),
        total_equity=Decimal("1000"),
        quote_asset="USDT",
        status=SourcePositionDataStatus.authentication_required,
        reason="fixture account-only endpoint",
        source_endpoint="fixture",
    )

    exposure = derive_perp_vault_exposure(bundle)

    assert exposure.long_notional is None
    assert exposure.short_notional is None
    assert exposure.open_position_count is None
    assert exposure.largest_position_notional is None


def test_account_only_observations_append_and_export_null_exposure() -> None:
    """Repeated unavailable observations remain visible through the price join."""
    connection = duckdb.connect(":memory:")
    try:
        initialise_perp_vault_observation_schema(connection)
        identity = PerpVaultIdentity("test-perp", "mainnet", "vault-1", TEST_PERP_CHAIN_ID, "vault-1")
        first = create_unavailable_perp_vault_observation_bundle(identity, _dt("2026-07-24T12:00:00"), Decimal("1000"), "USDT", SourcePositionDataStatus.authentication_required, "fixture", "fixture")
        second = create_unavailable_perp_vault_observation_bundle(identity, _dt("2026-07-24T13:00:00"), Decimal("1100"), "USDT", SourcePositionDataStatus.authentication_required, "fixture", "fixture")
        write_perp_vault_observation_bundle(connection, first, {"response": "first"})
        write_perp_vault_observation_bundle(connection, second, {"response": "second"})
        accounts, positions = read_perp_vault_observations(connection)

        snapshots = derive_perp_vault_metric_snapshots(accounts, positions)
        prices = pd.DataFrame({"chain": [TEST_PERP_CHAIN_ID], "address": ["vault-1"], "timestamp": [_dt("2026-07-24T13:01:00")]})
        exported = finalise_perp_metric_columns(attach_perp_metrics_to_price_rows(prices, snapshots), {(TEST_PERP_CHAIN_ID, "vault-1")})

        assert len(accounts) == EXPECTED_OPEN_POSITION_COUNT
        assert exported.iloc[0]["perp_position_data_status"] == SourcePositionDataStatus.authentication_required.value
        assert pd.isna(exported.iloc[0]["perp_open_position_count"])
        assert pd.isna(exported.iloc[0]["perp_long_notional"])
    finally:
        connection.close()


def test_storage_correction_does_not_retain_superseded_positions() -> None:
    """A later immutable bundle replaces rather than unions a correction set."""
    connection = duckdb.connect(":memory:")
    try:
        initialise_perp_vault_observation_schema(connection)
        first = _bundle("first", _dt("2026-07-24T12:01:00"), ("100", "-40"))
        corrected = _bundle("corrected", _dt("2026-07-24T12:02:00"), ("55",))
        write_perp_vault_observation_bundle(connection, first, {"response": "first"})
        write_perp_vault_observation_bundle(connection, corrected, {"response": "corrected"})

        accounts, positions = read_perp_vault_observations(connection)
        snapshots = derive_perp_vault_metric_snapshots(accounts, positions)

        assert len(snapshots) == 1
        assert snapshots.iloc[0]["perp_long_notional"] == EXPECTED_CORRECTED_LONG
        assert snapshots.iloc[0]["perp_short_notional"] == 0.0
        assert snapshots.iloc[0]["perp_open_position_count"] == 1
    finally:
        connection.close()


def test_equal_rank_conflicting_corrections_fail() -> None:
    """A non-deterministic correction set must stop the pipeline."""
    connection = duckdb.connect(":memory:")
    try:
        initialise_perp_vault_observation_schema(connection)
        written_at = _dt("2026-07-24T12:01:00")
        write_perp_vault_observation_bundle(connection, _bundle("one", written_at, ("100",)), {"response": "one"})
        write_perp_vault_observation_bundle(connection, _bundle("two", written_at, ("200",)), {"response": "two"})
        accounts, positions = read_perp_vault_observations(connection)

        with pytest.raises(ValueError, match="Ambiguous"):
            derive_perp_vault_metric_snapshots(accounts, positions)
    finally:
        connection.close()


def test_stale_metrics_retain_values_and_measurement_timestamp() -> None:
    """Stale values remain auditable through their status and observation time."""
    connection = duckdb.connect(":memory:")
    try:
        initialise_perp_vault_observation_schema(connection)
        bundle = _bundle("snapshot", _dt("2026-07-24T12:01:00"), ("100",))
        write_perp_vault_observation_bundle(connection, bundle, {"response": "snapshot"})
        accounts, positions = read_perp_vault_observations(connection)
        snapshots = derive_perp_vault_metric_snapshots(accounts, positions)
        prices = pd.DataFrame(
            {
                "chain": [TEST_PERP_CHAIN_ID, TEST_PERP_CHAIN_ID],
                "address": ["vault-1", "vault-1"],
                "timestamp": [_dt("2026-07-24T11:59:00"), _dt("2026-07-24T19:00:00")],
            }
        )

        joined = attach_perp_metrics_to_price_rows(prices, snapshots)

        assert pd.isna(joined.iloc[0]["perp_long_notional"])
        assert joined.iloc[1]["perp_long_notional"] == EXPECTED_LONG_NOTIONAL
        finalised = finalise_perp_metric_columns(joined, {(TEST_PERP_CHAIN_ID, "vault-1")}, PERP_METRICS_MAX_AGE)
        assert finalised.iloc[1]["perp_position_data_status"] == "stale"
        assert finalised.iloc[1]["perp_long_notional"] == EXPECTED_LONG_NOTIONAL
        assert finalised.iloc[1]["perp_metrics_observed_at"] == pd.Timestamp("2026-07-24T12:00:00")

        exported = build_perp_dex_other_data(finalised.iloc[1])
        assert exported is not None
        assert exported["position_data_status"] == "stale"
        assert exported["long_notional"] == EXPECTED_LONG_NOTIONAL
        assert exported["observed_at"] == "2026-07-24T12:00:00"
    finally:
        connection.close()


def test_forward_fill_keeps_stale_metrics_with_original_timestamp() -> None:
    """Hourly display filling keeps measurable values and their actual age."""
    frame = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID, TEST_PERP_CHAIN_ID],
            "address": ["vault-1", "vault-1"],
            "share_price": [1.0, 1.0],
            "perp_long_notional": [100.0, float("nan")],
            "perp_short_notional": [40.0, float("nan")],
            "perp_open_position_count": pd.array([2, pd.NA], dtype="Int64"),
            "perp_largest_position_notional": [100.0, float("nan")],
            "perp_quote_asset": ["USDC", "USDC"],
            "perp_position_data_status": ["available", "stale"],
            "perp_metrics_observed_at": [_dt("2026-07-24T00:00:00"), _dt("2026-07-24T00:00:00")],
        },
        index=pd.to_datetime(["2026-07-24T00:00:00", "2026-07-24T07:00:00"]),
    )

    filled = forward_fill_vault(frame)

    assert filled.iloc[-1]["perp_position_data_status"] == "stale"
    assert filled.iloc[-1]["perp_long_notional"] == EXPECTED_LONG_NOTIONAL
    assert filled.iloc[-1]["perp_open_position_count"] == EXPECTED_OPEN_POSITION_COUNT
    assert filled.iloc[-1]["perp_metrics_observed_at"] == pd.Timestamp("2026-07-24T00:00:00")


def test_observation_timestamp_uses_second_resolution() -> None:
    """Collector microseconds are intentionally truncated in the Parquet view."""
    connection = duckdb.connect(":memory:")
    try:
        initialise_perp_vault_observation_schema(connection)
        bundle = _bundle(
            "subsecond",
            _dt("2026-07-24T12:01:00.987654"),
            ("100",),
            observed_at=_dt("2026-07-24T12:00:00.987654"),
        )
        write_perp_vault_observation_bundle(connection, bundle, {"response": "snapshot"})
        accounts, positions = read_perp_vault_observations(connection)

        snapshots = derive_perp_vault_metric_snapshots(accounts, positions)

        assert snapshots.iloc[0]["perp_metrics_observed_at"] == pd.Timestamp("2026-07-24T12:00:00")
        assert snapshots["perp_metrics_observed_at"].dtype == "datetime64[ms]"
    finally:
        connection.close()


def test_latest_daily_price_row_aligns_newer_account_observation() -> None:
    """A current account read attaches only to the latest delayed price row."""
    connection = duckdb.connect(":memory:")
    try:
        initialise_perp_vault_observation_schema(connection)
        bundle = _bundle(
            "daily-alignment",
            _dt("2026-07-24T12:01:00"),
            ("100",),
            observed_at=_dt("2026-07-24T12:00:00"),
        )
        write_perp_vault_observation_bundle(connection, bundle, {"response": "snapshot"})
        accounts, positions = read_perp_vault_observations(connection)
        snapshots = derive_perp_vault_metric_snapshots(accounts, positions)
        prices = pd.DataFrame(
            {
                "chain": [TEST_PERP_CHAIN_ID, TEST_PERP_CHAIN_ID],
                "address": ["vault-1", "vault-1"],
                "timestamp": [_dt("2026-07-23T00:00:00"), _dt("2026-07-24T00:00:00")],
            }
        )

        joined = attach_perp_metrics_to_price_rows(prices, snapshots)
        finalised = finalise_perp_metric_columns(joined, {(TEST_PERP_CHAIN_ID, "vault-1")})

        assert pd.isna(finalised.iloc[0]["perp_long_notional"])
        assert finalised.iloc[1]["perp_long_notional"] == EXPECTED_LONG_NOTIONAL
        assert finalised.iloc[1]["perp_position_data_status"] == "available"
        assert finalised.iloc[1]["perp_metrics_observed_at"] == pd.Timestamp("2026-07-24T12:00:00")
    finally:
        connection.close()


def test_price_join_normalises_internal_chain_key_types() -> None:
    """Native ``int32`` price chains join ``int64`` DuckDB snapshot chains.

    The public chain column retains the source price-frame type; only the
    helper's temporary key is normalised for Pandas' strict as-of join.
    """
    snapshots = pd.DataFrame(
        {
            "chain": pd.Series([TEST_PERP_CHAIN_ID], dtype="int64"),
            "address": ["vault-1"],
            "position_effective_at": [_dt("2026-07-24T00:00:00")],
            "perp_long_notional": [EXPECTED_LONG_NOTIONAL],
            "perp_short_notional": [0.0],
            "perp_open_position_count": pd.array([1], dtype="Int64"),
            "perp_largest_position_notional": [EXPECTED_LONG_NOTIONAL],
            "perp_quote_asset": ["USDC"],
            "perp_position_data_status": ["available"],
            "perp_metrics_observed_at": [_dt("2026-07-24T00:00:00")],
        }
    )
    prices = pd.DataFrame(
        {
            "chain": pd.Series([TEST_PERP_CHAIN_ID], dtype="int32"),
            "address": ["vault-1"],
            "timestamp": [_dt("2026-07-24T00:01:00")],
        }
    )

    joined = attach_perp_metrics_to_price_rows(prices, snapshots)

    assert joined["chain"].dtype == "int32"
    assert joined.iloc[0]["perp_long_notional"] == EXPECTED_LONG_NOTIONAL


def test_latest_price_alignment_is_bounded() -> None:
    """An observation beyond the generic alignment window remains unattached."""
    snapshots = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID],
            "address": ["vault-1"],
            "position_effective_at": [_dt("2026-07-26T00:00:01")],
            "perp_long_notional": [100.0],
            "perp_short_notional": [0.0],
            "perp_open_position_count": pd.array([1], dtype="Int64"),
            "perp_largest_position_notional": [100.0],
            "perp_quote_asset": ["USDC"],
            "perp_position_data_status": ["available"],
            "perp_metrics_observed_at": [_dt("2026-07-26T00:00:01")],
        }
    )
    prices = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID],
            "address": ["vault-1"],
            "timestamp": [_dt("2026-07-24T00:00:00")],
        }
    )

    joined = attach_perp_metrics_to_price_rows(prices, snapshots, PERP_METRICS_MAX_FORWARD_ALIGNMENT)

    assert pd.isna(joined.iloc[0]["perp_long_notional"])


def test_alignment_uses_newest_eligible_status_and_clears_old_values() -> None:
    """A newer unavailable observation replaces an earlier available state.

    The observation beyond the alignment window must not hide the newest
    eligible status, and null numeric fields must overwrite the previous
    backward-joined exposure.
    """
    snapshots = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID, TEST_PERP_CHAIN_ID, TEST_PERP_CHAIN_ID],
            "address": ["vault-1", "vault-1", "vault-1"],
            "position_effective_at": pd.to_datetime(
                [
                    "2026-07-23T23:00:00",
                    "2026-07-24T12:00:00",
                    "2026-07-26T00:00:01",
                ]
            ),
            "perp_long_notional": [100.0, float("nan"), 200.0],
            "perp_short_notional": [0.0, float("nan"), 0.0],
            "perp_open_position_count": pd.array([1, pd.NA, 1], dtype="Int64"),
            "perp_largest_position_notional": [100.0, float("nan"), 200.0],
            "perp_quote_asset": ["USDC", "USDC", "USDC"],
            "perp_position_data_status": ["available", "authentication_required", "available"],
            "perp_metrics_observed_at": pd.to_datetime(
                [
                    "2026-07-23T23:00:00",
                    "2026-07-24T12:00:00",
                    "2026-07-26T00:00:01",
                ]
            ),
        }
    )
    prices = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID],
            "address": ["vault-1"],
            "timestamp": [_dt("2026-07-24T00:00:00")],
        }
    )

    joined = attach_perp_metrics_to_price_rows(prices, snapshots)

    assert joined.iloc[0]["perp_position_data_status"] == "authentication_required"
    assert pd.isna(joined.iloc[0]["perp_long_notional"])
    assert pd.isna(joined.iloc[0]["perp_open_position_count"])
    assert joined.iloc[0]["perp_metrics_observed_at"] == pd.Timestamp("2026-07-24T12:00:00")


def test_forward_alignment_preserves_price_address_case() -> None:
    """Metric joining must not rewrite a raw price identity.

    Address comparison is case-insensitive for native account matching, while
    the persisted price value remains byte-for-byte unchanged.
    """
    snapshots = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID],
            "address": ["vault-1"],
            "position_effective_at": [_dt("2026-07-24T12:00:00")],
            "perp_long_notional": [100.0],
            "perp_short_notional": [0.0],
            "perp_open_position_count": pd.array([1], dtype="Int64"),
            "perp_largest_position_notional": [100.0],
            "perp_quote_asset": ["USDC"],
            "perp_position_data_status": ["available"],
            "perp_metrics_observed_at": [_dt("2026-07-24T12:00:00")],
        }
    )
    prices = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID],
            "address": ["VaUlT-1"],
            "timestamp": [_dt("2026-07-24T00:00:00")],
        }
    )

    joined = attach_perp_metrics_to_price_rows(prices, snapshots)

    assert joined.iloc[0]["address"] == "VaUlT-1"
    assert joined.iloc[0]["perp_long_notional"] == EXPECTED_LONG_NOTIONAL


def test_forward_alignment_does_not_overlay_source_error() -> None:
    """A failed future poll must not erase a valid as-of measurement.

    ``source_error`` has no measured position state to align backwards onto a
    delayed price row, so the ordinary available snapshot remains attached.
    """
    snapshots = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID, TEST_PERP_CHAIN_ID],
            "address": ["vault-1", "vault-1"],
            "position_effective_at": pd.to_datetime(["2026-07-23T23:00:00", "2026-07-24T12:00:00"]),
            "perp_long_notional": [100.0, float("nan")],
            "perp_short_notional": [0.0, float("nan")],
            "perp_open_position_count": pd.array([1, pd.NA], dtype="Int64"),
            "perp_largest_position_notional": [100.0, float("nan")],
            "perp_quote_asset": ["USDC", "USDC"],
            "perp_position_data_status": ["available", "source_error"],
            "perp_metrics_observed_at": pd.to_datetime(["2026-07-23T23:00:00", "2026-07-24T12:00:00"]),
        }
    )
    prices = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID],
            "address": ["vault-1"],
            "timestamp": [_dt("2026-07-24T00:00:00")],
        }
    )

    joined = attach_perp_metrics_to_price_rows(prices, snapshots)

    assert joined.iloc[0]["perp_position_data_status"] == "available"
    assert joined.iloc[0]["perp_long_notional"] == EXPECTED_LONG_NOTIONAL
    assert joined.iloc[0]["perp_metrics_observed_at"] == pd.Timestamp("2026-07-23T23:00:00")


def test_finaliser_rejects_observation_beyond_forward_window() -> None:
    """A corrupt far-future measurement remains a hard pipeline error.

    Only the explicit two-day delayed-price alignment window may produce a
    negative row-relative age.
    """
    frame = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID],
            "address": ["vault-1"],
            "timestamp": [_dt("2026-07-24T00:00:00")],
            "perp_long_notional": [100.0],
            "perp_short_notional": [0.0],
            "perp_open_position_count": pd.array([1], dtype="Int64"),
            "perp_largest_position_notional": [100.0],
            "perp_quote_asset": ["USDC"],
            "perp_position_data_status": ["available"],
            "perp_metrics_observed_at": [_dt("2026-07-26T00:00:01")],
        }
    )

    with pytest.raises(ValueError, match="maximum forward-alignment window"):
        finalise_perp_metric_columns(frame, {(TEST_PERP_CHAIN_ID, "vault-1")})


def test_parquet_normalisation_rejects_non_fundamental_values() -> None:
    """Materialised fundamentals cannot contain impossible numeric values.

    Nulls remain valid for unavailable data, while infinity and negative
    long/short/largest/count values abort before reaching Parquet.
    """
    frame = pd.DataFrame(
        {
            "perp_long_notional": [float("inf")],
            "perp_short_notional": [0.0],
            "perp_open_position_count": pd.array([1], dtype="Int64"),
            "perp_largest_position_notional": [100.0],
            "perp_quote_asset": ["USDC"],
            "perp_position_data_status": ["available"],
            "perp_metrics_observed_at": [_dt("2026-07-24T00:00:00")],
        }
    )

    with pytest.raises(ValueError, match="finite monetary values"):
        normalise_perp_metric_parquet_dtypes(frame)


def test_price_join_rejects_duplicate_snapshot_keys() -> None:
    """Duplicate selected snapshots remain a hard pipeline error.

    The delayed-feed alignment must not de-duplicate ambiguous input before
    the ordinary temporal join validates its identity/effective-time keys.
    """
    snapshot = {
        "chain": TEST_PERP_CHAIN_ID,
        "address": "vault-1",
        "position_effective_at": _dt("2026-07-24T00:00:00"),
        "perp_long_notional": 100.0,
        "perp_short_notional": 0.0,
        "perp_open_position_count": 1,
        "perp_largest_position_notional": 100.0,
        "perp_quote_asset": "USDC",
        "perp_position_data_status": "available",
        "perp_metrics_observed_at": _dt("2026-07-24T00:00:00"),
    }
    snapshots = pd.DataFrame([snapshot, snapshot])
    prices = pd.DataFrame(
        {
            "chain": [TEST_PERP_CHAIN_ID],
            "address": ["vault-1"],
            "timestamp": [_dt("2026-07-24T00:00:00")],
        }
    )

    with pytest.raises(ValueError, match="Duplicate selected perp metric snapshots"):
        attach_perp_metrics_to_price_rows(prices, snapshots)


def test_lighter_adapter_signs_absolute_position_values() -> None:
    """Lighter's side field determines the sign of its absolute value."""
    bundle, _ = build_lighter_pool_observation_bundle(
        {
            "account_index": "42",
            "total_asset_value": "1000",
            "positions": [
                {"market_id": 1, "position": "2", "sign": 0, "position_value": "100"},
                {"market_id": 2, "position": "3", "sign": -1, "position_value": "40"},
                {"market_id": 3, "position": "0", "sign": 0, "position_value": "999"},
            ],
        },
        LIGHTER_ETHEREUM,
        _dt("2026-07-24T12:00:00"),
    )

    exposure = derive_perp_vault_exposure(bundle)

    assert exposure.long_notional == Decimal("100")
    assert exposure.short_notional == Decimal("40")
    assert exposure.open_position_count == EXPECTED_OPEN_POSITION_COUNT


def test_hyperliquid_adapter_signs_position_value_from_szi() -> None:
    """Hyperliquid's positive value becomes short when signed size is negative."""
    summary = VaultSummary(
        name="Fixture",
        vault_address="0x0000000000000000000000000000000000000001",
        leader="0x0000000000000000000000000000000000000002",
        tvl=Decimal("1000"),
        is_closed=False,
        relationship_type="normal",
    )
    state = PerpClearinghouseState(
        margin_summary=MarginSummary(Decimal("1000"), Decimal("0"), Decimal("0"), Decimal("0")),
        withdrawable=Decimal("0"),
        asset_positions=[
            AssetPosition("BTC", Decimal("1"), None, Decimal(0), Decimal(0), Decimal("70"), None),
            AssetPosition("ETH", Decimal("-2"), None, Decimal(0), Decimal(0), Decimal("30"), None),
        ],
    )

    bundle, _ = build_hyperliquid_vault_observation_bundle(summary, state, _dt("2026-07-24T12:00:00"))
    exposure = derive_perp_vault_exposure(bundle)

    assert exposure.long_notional == Decimal("70")
    assert exposure.short_notional == Decimal("30")


def test_pacifica_adapter_values_signed_positions_at_public_marks() -> None:
    """Unsupported Pacifica groundwork retains its tested sign conversion."""
    assert PACIFICA_PERP_VAULT_METRICS_SUPPORTED is False

    bundle, _ = build_pacifica_lake_observation_bundle(
        {"address": "LakePublicKey"},
        {"account_equity": "1000", "updated_at": 1784980800000},
        (
            {"symbol": "BTC", "side": "bid", "amount": "2"},
            {"symbol": "ETH", "side": "ask", "amount": "3"},
            {"symbol": "SOL", "side": "bid", "amount": "0"},
        ),
        {
            "BTC": (Decimal("100"), _dt("2026-07-24T12:00:00")),
            "ETH": (Decimal("20"), _dt("2026-07-24T12:00:00")),
        },
        _dt("2026-07-24T12:00:01"),
    )

    exposure = derive_perp_vault_exposure(bundle)

    assert exposure.long_notional == Decimal("200")
    assert exposure.short_notional == Decimal("60")
    assert exposure.open_position_count == EXPECTED_OPEN_POSITION_COUNT


def test_raw_schema_migration_preserves_perp_capability_metadata() -> None:
    """A scanner migration may remove pandas metadata but not the registry."""
    registry = PerpDexCapabilityRegistry((PerpDexCapability("test", "mainnet", "USDC", True, "available", 60, 60),))
    table = pa.table({"chain": pa.array([1], type=pa.uint32())})
    table = table.replace_schema_metadata(embed_perp_capability_registry(table.schema, registry).metadata)

    migrated = VaultHistoricalRead.migrate_parquet_schema(table)

    assert migrated.schema.metadata is not None
    assert migrated.schema.metadata[b"perp_dex.capability_registry"] == registry.to_json().encode("utf-8")


def test_json_export_handles_nullable_pandas_position_count() -> None:
    """Uncollected legacy metrics must remain JSON-compatible nulls."""
    exported = build_perp_dex_other_data(
        {
            "perp_position_data_status": "not_collected",
            "perp_open_position_count": pd.NA,
            "perp_quote_asset": "USDC",
            "perp_metrics_observed_at": pd.NaT,
        }
    )

    assert exported is not None
    assert exported["open_position_count"] is None
    assert exported["observed_at"] is None


def test_json_export_requires_measurement_timestamp_with_values() -> None:
    """Available and stale exports cannot separate values from measurement time."""
    with pytest.raises(ValueError, match="requires perp_metrics_observed_at"):
        build_perp_dex_other_data(
            {
                "perp_position_data_status": "available",
                "perp_long_notional": 100.0,
                "perp_short_notional": 0.0,
                "perp_open_position_count": 1,
                "perp_largest_position_notional": 100.0,
                "perp_quote_asset": "USDC",
                "perp_metrics_observed_at": pd.NaT,
            }
        )

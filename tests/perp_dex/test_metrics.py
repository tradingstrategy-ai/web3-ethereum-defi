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
from eth_defi.pacifica.perp_metrics import build_pacifica_lake_observation_bundle
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
    attach_perp_metrics_to_price_rows,
    derive_perp_vault_metric_snapshots,
    finalise_perp_metric_columns,
)
from eth_defi.perp_dex.storage import (
    initialise_perp_vault_observation_schema,
    read_perp_vault_observations,
    write_perp_vault_observation_bundle,
)
from eth_defi.vault.base import VaultHistoricalRead

EXPECTED_CORRECTED_LONG = 55.0
EXPECTED_LONG_NOTIONAL = 100.0
EXPECTED_OPEN_POSITION_COUNT = 2


def _dt(value: str) -> datetime.datetime:
    """Create the repository's canonical naive UTC fixture timestamp."""
    return datetime.datetime.fromisoformat(value)


def _bundle(
    snapshot_id: str,
    written_at: datetime.datetime,
    notionals: tuple[str, ...] = ("100", "-40"),
    status: SourcePositionDataStatus = SourcePositionDataStatus.available,
    collector_version: str = "1.0",
) -> PerpVaultObservationBundle:
    """Create a valid fixture bundle with a stable effective timestamp."""
    observed_at = _dt("2026-07-24T12:00:00")
    identity = PerpVaultIdentity("test-perp", "mainnet", "vault-1", 9994, "vault-1")
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
        identity=PerpVaultIdentity("test-perp", "mainnet", "vault-1", 9994, "vault-1"),
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
        identity = PerpVaultIdentity("test-perp", "mainnet", "vault-1", 9994, "vault-1")
        first = create_unavailable_perp_vault_observation_bundle(identity, _dt("2026-07-24T12:00:00"), Decimal("1000"), "USDT", SourcePositionDataStatus.authentication_required, "fixture", "fixture")
        second = create_unavailable_perp_vault_observation_bundle(identity, _dt("2026-07-24T13:00:00"), Decimal("1100"), "USDT", SourcePositionDataStatus.authentication_required, "fixture", "fixture")
        write_perp_vault_observation_bundle(connection, first, {"response": "first"})
        write_perp_vault_observation_bundle(connection, second, {"response": "second"})
        accounts, positions = read_perp_vault_observations(connection)

        snapshots = derive_perp_vault_metric_snapshots(accounts, positions)
        prices = pd.DataFrame({"chain": [9994], "address": ["vault-1"], "timestamp": [_dt("2026-07-24T13:01:00")]})
        exported = finalise_perp_metric_columns(attach_perp_metrics_to_price_rows(prices, snapshots), {(9994, "vault-1")})

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


def test_asof_join_is_backward_and_finaliser_is_the_only_stale_owner() -> None:
    """A stale value remains joinable until the row-relative finalisation pass."""
    connection = duckdb.connect(":memory:")
    try:
        initialise_perp_vault_observation_schema(connection)
        bundle = _bundle("snapshot", _dt("2026-07-24T12:01:00"), ("100",))
        write_perp_vault_observation_bundle(connection, bundle, {"response": "snapshot"})
        accounts, positions = read_perp_vault_observations(connection)
        snapshots = derive_perp_vault_metric_snapshots(accounts, positions)
        prices = pd.DataFrame(
            {
                "chain": [9994, 9994],
                "address": ["vault-1", "vault-1"],
                "timestamp": [_dt("2026-07-24T11:59:00"), _dt("2026-07-24T19:00:00")],
            }
        )

        joined = attach_perp_metrics_to_price_rows(prices, snapshots)

        assert pd.isna(joined.iloc[0]["perp_long_notional"])
        assert joined.iloc[1]["perp_long_notional"] == EXPECTED_LONG_NOTIONAL
        finalised = finalise_perp_metric_columns(joined, {(9994, "vault-1")}, PERP_METRICS_MAX_AGE)
        assert finalised.iloc[1]["perp_position_data_status"] == "stale"
        assert pd.isna(finalised.iloc[1]["perp_long_notional"])
    finally:
        connection.close()


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
    """Pacifica base amounts use current marks and the bid/ask side convention."""
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

"""Test vault metrics calculations and charts."""

import datetime
import json
import os.path
import pickle
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
import zstandard as zstd
from plotly.graph_objects import Figure

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.vault import D2_PROTOCOL_NAME, format_d2_vault_note
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.lighter.constants import LIGHTER_ETHEREUM, LIGHTER_ROBINHOOD, LighterAPIConfig
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.research import vault_metrics
from eth_defi.research.sparkline import export_sparkline_as_png, export_sparkline_as_svg, extract_vault_price_data, render_sparkline_simple
from eth_defi.research.vault_benchmark import visualise_vault_return_benchmark
from eth_defi.research.vault_metrics import (
    CryptoUSDConversionContext,
    PeriodMetrics,
    apply_abnormal_value_checks,
    apply_morpho_not_in_api_check,
    calculate_annualised_volatility_from_daily_returns,
    calculate_crypto_usd_period_results,
    calculate_hourly_returns_for_all_vaults,
    calculate_lifetime_metrics,
    calculate_period_metrics,
    calculate_returns,
    calculate_sharpe_ratio_from_returns,
    display_vault_chart_and_tearsheet,
    export_lifetime_row,
    format_lifetime_table,
    make_vault_display_flags,
    prepare_daily_share_price_series,
    resample_returns,
)
from eth_defi.vault.base import VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import NOT_IN_MORPHO_API, VaultFlag
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase


def test_sparse_share_prices_are_regularised_once_for_daily_risk_metrics() -> None:
    """Accept forward filling as the common sparse-series approximation."""

    observations = pd.Series(
        [100.0, 121.0],
        index=pd.to_datetime(["2026-01-01 12:00:00", "2026-01-04 12:00:00"]),
    )

    daily_prices, daily_returns = prepare_daily_share_price_series(observations)

    # The accepted approximation makes unobserved days flat and assigns the
    # complete intervening movement to the next observed day.
    assert daily_prices.tolist() == [100.0, 100.0, 100.0, 121.0]
    assert pd.isna(daily_returns.iloc[0])
    assert daily_returns.iloc[1:].tolist() == pytest.approx([0.0, 0.0, 0.21])
    assert calculate_annualised_volatility_from_daily_returns(daily_returns) == pytest.approx(daily_returns.dropna().std() * 365**0.5)
    assert calculate_sharpe_ratio_from_returns(daily_returns) == pytest.approx(daily_returns.dropna().mean() / daily_returns.dropna().std() * 365**0.5)


def test_period_metrics_rejects_empty_share_price_series_cleanly() -> None:
    """A raw vault group without usable prices must not abort the batch."""

    empty = pd.Series(index=pd.DatetimeIndex([]), dtype="float64")
    fees = FeeData(fee_mode=VaultFeeMode.feeless, management=0, performance=0, deposit=0, withdraw=0)

    result = calculate_period_metrics(
        period="lifetime",
        gross_fee_data=fees,
        net_fee_data=fees,
        share_price_hourly=empty,
        share_price_daily=empty,
        daily_returns=empty,
        tvl=empty,
        now_=pd.Timestamp("2026-01-01"),
    )

    assert result.error_reason == "Vault has no usable share-price observations"
    assert result.raw_samples == 0


def test_usd_period_fee_path_does_not_charge_performance_fee_on_eth_appreciation() -> None:
    """USD fee composition applies externalised fees to native vault returns only."""
    index = pd.to_datetime(["2026-01-01", "2026-01-05"])
    native_prices = pd.Series([1.0, 1.0], index=index)
    usd_prices = pd.Series([1_000.0, 2_000.0], index=index)
    daily_usd_prices, daily_usd_returns = prepare_daily_share_price_series(usd_prices)
    native_fees = FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=0.0,
        performance=0.2,
        deposit=0.0,
        withdraw=0.0,
    )
    usd_metrics = calculate_period_metrics(
        period="lifetime",
        gross_fee_data=native_fees,
        net_fee_data=native_fees,
        share_price_hourly=usd_prices,
        share_price_daily=daily_usd_prices,
        daily_returns=daily_usd_returns,
        tvl=pd.Series([1_000.0, 2_000.0], index=index),
        now_=index[-1],
        native_fee_share_price=native_prices,
        exchange_rate=pd.Series([1_000.0, 2_000.0], index=index),
    )

    assert usd_metrics.returns_gross == pytest.approx(1.0)
    assert usd_metrics.returns_net == pytest.approx(1.0)
    assert usd_metrics.tvl_end == pytest.approx(2_000.0)


def test_usd_period_metrics_include_underlying_exchange_rate_endpoints() -> None:
    """BTC/ETH USD metrics expose the precise underlying prices they used."""
    index = pd.to_datetime(["2026-01-01", "2026-01-05"])
    native_prices = pd.Series([1.0, 1.0], index=index)
    daily_native_prices, _ = prepare_daily_share_price_series(native_prices)
    rates = pd.Series([1_000.0, 1_250.0, 1_500.0, 1_750.0, 2_000.0], index=pd.date_range("2026-01-01", "2026-01-05", freq="D"))
    fees = FeeData(fee_mode=VaultFeeMode.feeless, management=0.0, performance=0.0, deposit=0.0, withdraw=0.0)
    metrics = calculate_crypto_usd_period_results(
        context=CryptoUSDConversionContext(
            rates_by_family={"eth": rates},
            errors_by_family={},
            vault_families={"1-0xeth": "eth"},
        ),
        vault_id="1-0xeth",
        native_share_price_observations=native_prices,
        native_daily_share_prices=daily_native_prices,
        native_total_assets=pd.Series([2.0, 2.0], index=index),
        gross_fee_data=fees,
        net_fee_data=fees,
    )

    assert metrics is not None
    lifetime = next(metric for metric in metrics if metric.period == "lifetime")
    assert lifetime.exchange_rate_start == pytest.approx(1_000.0)
    assert lifetime.exchange_rate_end == pytest.approx(2_000.0)


def test_usd_period_metrics_restart_lifetime_after_long_rate_gap() -> None:
    """A rate gap longer than the bounded fill excludes older USD history."""
    index = pd.date_range("2026-01-01", "2026-01-12", freq="D")
    rates = pd.Series([1_000.0] * 3 + [float("nan")] * 4 + [2_000.0] * 5, index=index)
    observations = pd.Series([1.0, 1.0, 1.0], index=pd.to_datetime(["2026-01-01", "2026-01-08", "2026-01-12"]))
    daily_native_prices, _ = prepare_daily_share_price_series(observations)
    fees = FeeData(fee_mode=VaultFeeMode.feeless, management=0.0, performance=0.0, deposit=0.0, withdraw=0.0)

    metrics = calculate_crypto_usd_period_results(
        context=CryptoUSDConversionContext(
            rates_by_family={"eth": rates},
            errors_by_family={},
            vault_families={"1-0xeth": "eth"},
        ),
        vault_id="1-0xeth",
        native_share_price_observations=observations,
        native_daily_share_prices=daily_native_prices,
        native_total_assets=pd.Series([1.0, 1.0, 1.0], index=observations.index),
        gross_fee_data=fees,
        net_fee_data=fees,
    )

    assert metrics is not None
    lifetime = next(metric for metric in metrics if metric.period == "lifetime")
    assert lifetime.samples_start_at == pd.Timestamp("2026-01-08")
    assert lifetime.exchange_rate_start == pytest.approx(2_000.0)


def test_return_resampling_preserves_missing_calendar_days() -> None:
    """General return helpers produce zero returns for unobserved days."""

    index = pd.to_datetime(["2026-01-01 12:00:00", "2026-01-04 12:00:00"])
    prices = pd.Series([100.0, 121.0], index=index)
    sparse_returns = prices.pct_change(fill_method=None).fillna(0.0)

    assert calculate_returns(prices).tolist() == pytest.approx([0.0, 0.0, 0.0, 0.21])
    assert resample_returns(sparse_returns).tolist() == pytest.approx([0.0, 0.0, 0.0, 0.21])


def test_daily_vault_resampling_does_not_forward_fill_flow_totals() -> None:
    """Daily price filling must not duplicate deposits on missing days."""

    vault_id = "1-0x1234"
    rows = pd.DataFrame(
        {
            "chain": [1, 1],
            "address": ["0x1234", "0x1234"],
            "id": [vault_id, vault_id],
            "share_price": [100.0, 110.0],
            "daily_deposit_usd": [10.0, 20.0],
        },
        index=pd.to_datetime(["2026-01-01 12:00:00", "2026-01-03 12:00:00"]),
    )

    daily = calculate_hourly_returns_for_all_vaults(rows)

    assert daily["share_price"].tolist() == [100.0, 100.0, 110.0]
    assert daily["id"].tolist() == [vault_id, vault_id, vault_id]
    assert daily[vault_metrics.VAULT_STATE_OBSERVED_COLUMN].fillna(False).tolist() == [True, False, True]
    assert pd.isna(daily["daily_deposit_usd"].iloc[1])
    assert daily["daily_deposit_usd"].sum() == pytest.approx(30.0)


def test_erc4626_state_deltas_derive_estimated_net_flows() -> None:
    """Separate yield from netted ERC-4626 deposits and redemptions."""
    prices = pd.DataFrame(
        {
            "total_assets": [100.0, 111.1, 107.1],
            "total_supply": [100.0, 110.0, 105.0],
            "share_price": [1.0, 1.01, 1.02],
            vault_metrics.VAULT_STATE_OBSERVED_COLUMN: [True, True, True],
        },
        index=pd.date_range("2026-01-01", periods=3, freq="D"),
    )

    result = vault_metrics._derive_erc4626_estimated_daily_flows(prices)

    assert pd.isna(result[vault_metrics.FLOW_VALUE_COLUMN].iloc[0])
    assert result[vault_metrics.FLOW_VALUE_COLUMN].iloc[1] == pytest.approx(10.1)
    assert result[vault_metrics.FLOW_VALUE_COLUMN].iloc[2] == pytest.approx(-5.1)
    assert "daily_deposit_usd" not in result
    assert "daily_withdrawal_usd" not in result


def test_erc4626_state_deltas_reject_forward_filled_scanner_gaps() -> None:
    """Do not publish zero flow for a day without a scanner observation."""
    vault_id = "1-0x1234"
    sparse_prices = pd.DataFrame(
        {
            "chain": [1, 1],
            "address": ["0x1234", "0x1234"],
            "id": [vault_id, vault_id],
            "total_assets": [100.0, 122.4],
            "total_supply": [100.0, 120.0],
            "share_price": [1.0, 1.02],
        },
        index=pd.to_datetime(["2026-01-01 12:00:00", "2026-01-03 12:00:00"]),
    )
    daily_prices = calculate_hourly_returns_for_all_vaults(sparse_prices)

    result = vault_metrics._derive_erc4626_estimated_daily_flows(daily_prices)

    assert result[vault_metrics.FLOW_VALUE_COLUMN].isna().all()


def test_get_trading_strategy_links_use_canonical_vault_routes():
    """Generated Trading Strategy links use canonical flat vault routes."""
    vault_link = vault_metrics._get_trading_strategy_vault_link(
        vault_slug="texashedge",
    )

    assert vault_link == "https://tradingstrategy.ai/trading-view/vaults/texashedge"
    assert vault_metrics._get_trading_strategy_chain_link("Ethereum") == "https://tradingstrategy.ai/trading-view/vaults/chains/ethereum"
    assert vault_metrics._get_trading_strategy_chain_link("Hypercore") == "https://tradingstrategy.ai/trading-view/vaults/chains/hyperliquid"


def test_calculate_vault_rankings_includes_per_curator_ranks_at_one_hundred_dollars() -> None:
    """Rank eligible vaults within each curator while excluding smaller and unknown groups.

    Curator cohorts deliberately use the lower $100 TVL eligibility threshold,
    independently of the $10,000 threshold for chain and protocol cohorts.
    """
    results_df = pd.DataFrame(
        {
            "chain": ["Ethereum"] * 5,
            "protocol_slug": ["morpho"] * 5,
            "curator_slug": ["alpha", "alpha", "beta", "alpha", None],
            "risk": [VaultTechnicalRisk.negligible] * 5,
            "period_results": [
                [PeriodMetrics(period="1W", cagr_net=0.10, tvl_end=100)],
                [PeriodMetrics(period="1W", cagr_net=0.20, tvl_end=100)],
                [PeriodMetrics(period="1W", cagr_net=0.30, tvl_end=100)],
                [PeriodMetrics(period="1W", cagr_net=0.40, tvl_end=99)],
                [PeriodMetrics(period="1W", cagr_net=0.50, tvl_end=100)],
            ],
        },
        index=["alpha-lower", "alpha-higher", "beta", "alpha-too-small", "unknown"],
    )

    vault_metrics.calculate_vault_rankings(results_df)

    expected_lower_rank = 2
    expected_best_rank = 1
    assert results_df.loc["alpha-lower", "period_results"][0].ranking_curator == expected_lower_rank
    assert results_df.loc["alpha-higher", "period_results"][0].ranking_curator == expected_best_rank
    assert results_df.loc["beta", "period_results"][0].ranking_curator == expected_best_rank
    assert results_df.loc["alpha-too-small", "period_results"][0].ranking_curator is None
    assert results_df.loc["unknown", "period_results"][0].ranking_curator is None


def test_calculate_vault_rankings_excludes_disabled_gmx_products() -> None:
    """Keep disabled GMX history queryable without ranking it as investable."""

    active = PeriodMetrics(period="1W", cagr_net=0.10, tvl_end=100_000)
    disabled = PeriodMetrics(period="1W", cagr_net=0.20, tvl_end=100_000)
    results_df = pd.DataFrame(
        {
            "chain": ["Arbitrum", "Arbitrum"],
            "protocol": ["GMX", "GMX"],
            "protocol_slug": ["gmx", "gmx"],
            "curator_slug": ["gmx", "gmx"],
            "risk": [VaultTechnicalRisk.low, VaultTechnicalRisk.low],
            "deposit_closed_reason": [None, "GMX product disabled"],
            "period_results": [[active], [disabled]],
        },
    )

    vault_metrics.calculate_vault_rankings(results_df)

    assert active.ranking_overall == 1
    assert active.ranking_chain == 1
    assert active.ranking_protocol == 1
    assert active.ranking_curator == 1
    assert disabled.ranking_overall is None
    assert disabled.ranking_chain is None
    assert disabled.ranking_protocol is None
    assert disabled.ranking_curator is None


def test_calculate_net_profit_accepts_one_hundred_percent_performance_fee() -> None:
    """Euler vaults may charge the valid 100% performance-fee boundary."""
    start = pd.Timestamp("2026-01-01").to_pydatetime()
    end = pd.Timestamp("2026-01-02").to_pydatetime()

    net_profit = vault_metrics.calculate_net_profit(
        start=start,
        end=end,
        share_price_start=100.0,
        share_price_end=110.0,
        management_fee_annual=0.0,
        performance_fee=1.0,
        deposit_fee=0.0,
        withdrawal_fee=0.0,
    )

    assert net_profit == 0.0


def test_calculate_lifetime_metrics_skips_invalid_vault_record(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One persisted malformed fee record does not abort the full export."""
    valid_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    invalid_id = "43111-0x614eb485de3c6c49701b40806ac1b985ad6f0a2f"
    valid_spec = VaultSpec.parse_string(valid_id)
    invalid_spec = VaultSpec.parse_string(invalid_id)
    invalid_row = dict(vault_db.rows[invalid_spec])
    source_fees = invalid_row["_fees"]
    invalid_fees = FeeData(
        fee_mode=source_fees.fee_mode,
        management=source_fees.management,
        performance=source_fees.performance,
        deposit=source_fees.deposit,
        withdraw=source_fees.withdraw,
    )
    invalid_fees.performance = 1.01  # Simulate an old pickle that bypassed FeeData validation.
    invalid_row["_fees"] = invalid_fees

    metrics = calculate_lifetime_metrics(
        price_df.loc[price_df["id"].isin((valid_id, invalid_id))],
        {
            valid_spec: dict(vault_db.rows[valid_spec]),
            invalid_spec: invalid_row,
        },
    )

    assert metrics["id"].tolist() == [valid_id]
    assert f"Skipping invalid vault metrics record for {invalid_id}" in caplog.text


def test_calculate_lifetime_metrics_filters_pyarrow_nan_share_price(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """An IEEE NaN scanner result does not discard an otherwise valid vault.

    PyArrow stores an IEEE NaN as a double value instead of a nullable Arrow
    cell. The metric preparation must therefore explicitly use finite-value
    validation rather than relying on ``dropna()``.
    """
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)
    prices = price_df.loc[price_df["id"] == vault_id].copy()
    share_prices = prices["share_price"].astype("float64").to_numpy(copy=True)
    share_prices[-1] = float("nan")
    prices["share_price"] = pd.Series(pd.arrays.ArrowExtensionArray(pa.array(share_prices, type=pa.float64())), index=prices.index)
    assert prices["share_price"].isna().sum() == 0

    metrics = calculate_lifetime_metrics(prices, {vault_spec: dict(vault_db.rows[vault_spec])})

    assert metrics["id"].tolist() == [vault_id]
    lifetime = vault_metrics.get_period_metrics(metrics.iloc[0]["period_results"], "lifetime")
    assert lifetime is not None
    assert lifetime.error_reason is None
    assert lifetime.raw_samples == len(prices) - 1
    assert metrics.iloc[0]["last_share_price"] == pytest.approx(share_prices[-2])
    assert metrics.iloc[0]["last_updated_block"] == prices.iloc[-2]["block_number"]


def test_calculate_crypto_usd_period_results_filters_pyarrow_nan_share_price() -> None:
    """USD conversion uses valid endpoint prices after a PyArrow NaN read failure."""
    vault_id = "1-0x0000000000000000000000000000000000000001"
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    native_prices = pd.Series(
        pd.arrays.ArrowExtensionArray(pa.array([1.0, float("nan"), 1.02, 1.03, 1.04], type=pa.float64())),
        index=dates,
    )
    assert native_prices.isna().sum() == 0
    native_daily, _daily_returns = prepare_daily_share_price_series(native_prices)
    context = CryptoUSDConversionContext(
        rates_by_family=MappingProxyType({"eth": pd.Series(2_000.0, index=dates)}),
        errors_by_family=MappingProxyType({}),
        vault_families=MappingProxyType({vault_id: "eth"}),
    )
    fees = FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=0.0,
        performance=0.0,
        deposit=0.0,
        withdraw=0.0,
    )

    metrics = calculate_crypto_usd_period_results(
        context=context,
        vault_id=vault_id,
        native_share_price_observations=native_prices,
        native_daily_share_prices=native_daily,
        native_total_assets=pd.Series(10.0, index=dates),
        gross_fee_data=fees,
        net_fee_data=fees,
    )

    assert metrics is not None
    lifetime = vault_metrics.get_period_metrics(metrics, "lifetime")
    assert lifetime is not None
    assert lifetime.error_reason is None
    assert lifetime.raw_samples == len(native_prices) - 1
    assert lifetime.share_price_end == pytest.approx(2_080.0)


def test_export_lifetime_row_converts_non_finite_numpy_scalars_to_null() -> None:
    """Strict JSON export cannot be blocked by NumPy share-price sentinels."""
    exported = export_lifetime_row(pd.Series({"last_share_price": np.float64("nan"), "current_nav": np.float64("inf")}))

    assert exported["last_share_price"] is None
    assert exported["current_nav"] is None


def test_calculate_lifetime_metrics_prepares_daily_series_once_per_vault(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All period metrics reuse one daily price and return pair per vault."""

    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    spec = VaultSpec.parse_string(vault_id)
    calls = 0
    original = vault_metrics.prepare_daily_share_price_series

    def count_preparations(observations: pd.Series) -> tuple[pd.Series, pd.Series]:
        nonlocal calls
        calls += 1
        return original(observations)

    monkeypatch.setattr(vault_metrics, "prepare_daily_share_price_series", count_preparations)
    calculate_lifetime_metrics(price_df.loc[price_df["id"] == vault_id], {spec: dict(vault_db.rows[spec])})

    assert calls == 1


def test_net_return_calculations_accept_decimal_fees() -> None:
    """Cached Decimal fee metadata is compatible with float price returns."""
    start = pd.Timestamp("2026-01-01").to_pydatetime()
    end = pd.Timestamp("2026-01-02").to_pydatetime()
    expected_return = (1 - 0.01) * (1 + 0.1) * (1 - 0.02) - 1

    net_profit = vault_metrics.calculate_net_profit(
        start=start,
        end=end,
        share_price_start=100.0,
        share_price_end=110.0,
        management_fee_annual=Decimal("0"),
        performance_fee=Decimal("0"),
        deposit_fee=Decimal("0.01"),
        withdrawal_fee=Decimal("0.02"),
    )
    assert net_profit == pytest.approx(expected_return)

    net_returns = vault_metrics.calculate_net_returns_from_price(
        name="Decimal fee vault",
        share_price=pd.Series(
            [100.0, 110.0],
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        ),
        management_fee_annual=Decimal("0"),
        performance_fee=Decimal("0"),
        deposit_fee=Decimal("0.01"),
        withdrawal_fee=Decimal("0.02"),
    )
    assert net_returns.iloc[-1] == pytest.approx(expected_return)


def test_calculate_lifetime_metrics_normalises_pickled_decimal_fees(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Old pickled FeeData with Decimal values exports as floating-point fees."""
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[vault_spec])
    legacy_fees = FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=0.0,
        performance=0.0,
        deposit=0.0,
        withdraw=0.0,
    )
    legacy_fees.management = Decimal("0")
    legacy_fees.performance = Decimal("0.15")
    legacy_fees.deposit = Decimal("0.01")
    legacy_fees.withdraw = Decimal("0.02")
    vault_row["_fees"] = legacy_fees

    metrics = calculate_lifetime_metrics(
        price_df.loc[price_df["id"] == vault_id],
        {vault_spec: vault_row},
    )

    result = metrics.iloc[0]
    assert result["mgmt_fee"] == 0.0
    assert result["perf_fee"] == 0.15
    assert result["deposit_fee"] == 0.01
    assert result["withdraw_fee"] == 0.02


def test_calculate_lifetime_metrics_normalises_legacy_decimal_fee_columns(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Old metadata columns with Decimal fees can rebuild FeeData safely."""
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[vault_spec])
    vault_row["_fees"] = None
    vault_row["Mgmt fee"] = Decimal("0")
    vault_row["Perf fee"] = Decimal("0.15")
    vault_row["Deposit fee"] = Decimal("0.01")
    vault_row["Withdrawal fee"] = Decimal("0.02")

    metrics = calculate_lifetime_metrics(
        price_df.loc[price_df["id"] == vault_id],
        {vault_spec: vault_row},
    )

    result = metrics.iloc[0]
    assert result["mgmt_fee"] == 0.0
    assert result["perf_fee"] == 0.15
    assert result["deposit_fee"] == 0.01
    assert result["withdraw_fee"] == 0.02


def test_apply_morpho_not_in_api_check_blacklists_vault():
    """Morpho API missing flag blacklists the vault in metrics."""
    risk, notes, flags = apply_morpho_not_in_api_check(
        risk=VaultTechnicalRisk.negligible,
        notes=None,
        flags={VaultFlag.not_in_morpho_api},
    )

    assert risk == VaultTechnicalRisk.blacklisted
    assert notes == NOT_IN_MORPHO_API
    assert flags == {VaultFlag.not_in_morpho_api}


def test_apply_morpho_not_in_api_check_preserves_existing_note():
    """Morpho API missing flag does not replace higher-priority notes."""
    risk, notes, flags = apply_morpho_not_in_api_check(
        risk=VaultTechnicalRisk.negligible,
        notes="Existing manual note",
        flags={VaultFlag.not_in_morpho_api},
    )

    assert risk == VaultTechnicalRisk.blacklisted
    assert notes == "Existing manual note"
    assert flags == {VaultFlag.not_in_morpho_api}


def test_make_vault_display_flags_builds_generic_warning_contract() -> None:
    """Generic vault display flags use a compact JSON-safe warning contract.

    1. Build display flags from red and yellow warning type strings.
    2. Assert the output preserves severity, type and source in order.
    3. Assert empty inputs produce an empty list for clean vaults.
    """

    # 1. Build display flags from red and yellow warning type strings.
    display_flags = make_vault_display_flags(
        red_flags=["bad_debt_unrealized", "short_timelock"],
        yellow_flags=["not_whitelisted"],
        source="morpho",
    )

    # 2. The output preserves severity, type and source in order.
    assert display_flags == [
        {"severity": "red", "type": "bad_debt_unrealized", "source": "morpho"},
        {"severity": "red", "type": "short_timelock", "source": "morpho"},
        {"severity": "yellow", "type": "not_whitelisted", "source": "morpho"},
    ]

    # 3. Clean vaults do not emit display flags.
    assert make_vault_display_flags(red_flags=[], yellow_flags=[], source="morpho") == []


def test_get_latest_vault_poll_frequency_returns_newest_non_empty_value() -> None:
    """Latest vault scan cycle is read from the newest populated price row."""
    prices_df = pd.DataFrame(
        {
            "vault_poll_frequency": ["", None, pd.NA, "small_tvl", "peaked"],
        }
    )

    assert vault_metrics.get_latest_vault_poll_frequency(prices_df) == "peaked"
    assert vault_metrics.get_latest_vault_poll_frequency(pd.DataFrame({"share_price": [1.0]})) is None


def test_extend_notes_with_vault_scan_cycle_appends_lower_cycle_note() -> None:
    """Lower scan cycles add context without replacing existing notes."""
    notes = vault_metrics.extend_notes_with_vault_scan_cycle("Manual note", "peaked")

    assert notes.startswith("Manual note; ")
    assert "Manual note" in notes
    assert "The vault data might be updated infrequently because" in notes
    assert "historical peak" in notes

    small_tvl_notes = vault_metrics.extend_notes_with_vault_scan_cycle(None, "small_tvl")
    assert "The vault data might be updated infrequently because" in small_tvl_notes
    assert "active-vault threshold" in small_tvl_notes

    assert vault_metrics.extend_notes_with_vault_scan_cycle("Manual note", "large_tvl") == "Manual note"
    assert vault_metrics.extend_notes_with_vault_scan_cycle(None, None) is None


@pytest.fixture(scope="module")
def vault_db() -> VaultDatabase:
    """Load sample vault database for testing.

    To generate:

    .. code-block:: shell

        zstd -22 --ultra -f -o tests/research/vault-metadata-db.pickle.zstd ~/.tradingstrategy/vaults/vault-metadata-db.pickle

    """
    path = Path(os.path.dirname(__file__)) / "vault-metadata-db.pickle.zstd"
    with zstd.open(path, "rb") as f:
        return pickle.load(f)


@pytest.fixture(scope="module")
def price_df() -> pd.DataFrame:
    """Load price data for testing.

    - Use a small sample of Hemi chain data taken with extract-single-chain.py
    """

    path = Path(os.path.dirname(__file__)) / "chain-hemi-prices-1h.parquet"
    return pd.read_parquet(path)


# TODO: Rechecl data here
def test_calculate_lifetime_metrics(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Test lifetime metrics calculation."""

    hemi_vaults = [row for row in vault_db.values() if row["_detection_data"].chain == 43111]
    assert len(hemi_vaults) > 0, "No Hemi vaults found in test data"

    ids = price_df["id"].unique()
    assert set(ids) == {"43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883", "43111-0x614eb485de3c6c49701b40806ac1b985ad6f0a2f", "43111-0x1324285bb2ddadfc9bebc2f8fc5049d7985312c0"}

    metrics = calculate_lifetime_metrics(
        price_df,
        vault_db,
    )

    # We should get data for 4 vaults
    assert len(metrics) == 3

    sample_row = metrics.set_index("id").loc["43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"]
    assert sample_row["chain"] == "Hemi"
    assert sample_row["years"] == pytest.approx(0.11225188227241616)
    assert sample_row["name"] == "Clearstar USDC.e"

    assert sample_row["last_updated_at"] == pd.Timestamp("2025-10-24 06:34:11")
    assert sample_row["last_updated_block"] == 2_951_745

    assert sample_row["perf_fee"] == 0.15
    assert sample_row["mgmt_fee"] == 0
    assert sample_row["deposit_fee"] == 0
    assert sample_row["withdraw_fee"] == 0
    # assert sample_row["risk"] == VaultTechnicalRisk.negligible
    assert sample_row["current_nav"] == pytest.approx(2345373.103418)
    assert sample_row["fee_label"] == "0% / 15% (int.)"

    # Token decimals must be carried into the export from the on-chain
    # metadata. Regression: previously dropped entirely, which made downstream
    # consumers default a missing value to 18 and break raw-amount math (a
    # 6-decimal USDC burn scaled by 10**18 reverts on-chain). The denomination
    # token (USDC.e) is 6 decimals, distinct from the share token (CSUSDCE) at 18.
    assert sample_row["denomination_decimals"] == 6
    assert sample_row["share_token_decimals"] == 18

    assert sample_row["lifetime_return"] == pytest.approx(0.002758)
    assert sample_row["cagr"] == pytest.approx(0.02483940718068034)
    assert sample_row["cagr_net"] == pytest.approx(0.02483940718068034)

    # Three months metrics - the test data spans ~41 days, which fits within 3M tolerance
    assert sample_row["three_months_cagr"] == pytest.approx(0.02483940718068034)
    assert sample_row["three_months_cagr_net"] == pytest.approx(0.02483940718068034)
    # Sparse change-only observations are expanded to consecutive calendar
    # days before annualising the risk metrics.
    assert sample_row["three_months_sharpe"] == pytest.approx(16.510952815481623)
    assert sample_row["three_months_sharpe_net"] == pytest.approx(16.510952815481623)

    assert sample_row["one_month_returns"] == pytest.approx(0.0018523254977500514)
    assert sample_row["one_month_returns_net"] == pytest.approx(0.0018523254977500514)
    assert sample_row["one_month_cagr"] == pytest.approx(0.022786946472187264)
    assert sample_row["one_month_cagr_net"] == pytest.approx(0.022786946472187264)

    assert sample_row["features"] == ["morpho_like"]
    assert sample_row["protocol_slug"] == "morpho"
    assert sample_row["vault_slug"] == "clearstar-usdc-e"

    # Link feature was not in the sample data when generated
    assert sample_row["link"] is None

    # Verify period_results contains structured period metrics
    period_results = sample_row["period_results"]
    assert isinstance(period_results, list)
    assert len(period_results) == 6  # 1W, 1M, 3M, 6M, 1Y, lifetime
    assert sample_row["netflow"] is None
    assert all(period.flow_value is None for period in period_results)
    assert all(period.deposit_value is None for period in period_results)
    assert all(period.redeem_value is None for period in period_results)
    assert all(period.deposit_count is None for period in period_results)
    assert all(period.redemption_count is None for period in period_results)

    # Check one period (1M) from period_results
    one_month_result = next(p for p in period_results if p.period == "1M")
    assert isinstance(one_month_result, PeriodMetrics)
    assert one_month_result.period == "1M"
    # The 1M period should have data (matching legacy one_month_returns)
    assert one_month_result.raw_samples > 0

    # Check lifetime period
    lifetime_result = next(p for p in period_results if p.period == "lifetime")
    assert isinstance(lifetime_result, PeriodMetrics)
    assert lifetime_result.period == "lifetime"
    assert lifetime_result.raw_samples > 0
    # Lifetime returns should approximately match the legacy lifetime_return
    assert lifetime_result.returns_gross == pytest.approx(sample_row["lifetime_return"], rel=0.01)

    # Verify rankings are stored in PeriodMetrics objects for all periods
    for pm in period_results:
        assert hasattr(pm, "ranking_overall")
        assert hasattr(pm, "ranking_chain")
        assert hasattr(pm, "ranking_protocol")
        assert hasattr(pm, "ranking_curator")
        # avg_utilisation must be present on every period (None for non-lending vaults)
        assert hasattr(pm, "avg_utilisation")

    # # Check 3M period rankings - vault has valid CAGR and TVL so should have rankings
    # three_month_result = next(p for p in period_results if p.period == "3M")
    # # If CAGR is valid and TVL >= 10k, vault should have rankings
    # if three_month_result.cagr_net is not None and (three_month_result.tvl_end or 0) >= 10_000:
    #     assert three_month_result.ranking_overall is not None
    #     assert three_month_result.ranking_chain is not None
    #     assert three_month_result.ranking_protocol is not None
    # else:
    #     assert three_month_result.ranking_overall is None

    # Lending statistics columns should be present in raw metrics
    # (may be None/NaN if not available in test data)
    assert "available_liquidity" in metrics.columns
    assert "utilisation" in metrics.columns
    assert "vault_poll_frequency" in metrics.columns

    # We can get human readable output
    formatted = format_lifetime_table(
        metrics,
        add_index=True,
        add_address=True,
    )
    # assert len(formatted) == 3

    # Lending statistics should be present in formatted table with proper column names
    assert "Available liquidity" in formatted.columns
    assert "Utilisation" in formatted.columns
    assert "deposit_permission" not in formatted.columns
    assert "whitelist" not in formatted.columns

    # Verify period_results is not in formatted output
    # assert "period_results" not in formatted.columns


def test_morpho_daily_state_pipeline_exports_estimated_period_flows(vault_db: VaultDatabase, price_df: pd.DataFrame) -> None:
    """Export exact estimated flow values from scanned Morpho vault states."""
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    spec = VaultSpec.parse_string(vault_id)
    daily_prices = calculate_hourly_returns_for_all_vaults(price_df.loc[price_df["id"] == vault_id])

    metrics = calculate_lifetime_metrics(daily_prices, {spec: vault_db.rows[spec]})
    sample_row = metrics.iloc[0]
    period_results = sample_row["period_results"]
    one_week_flow = next(period for period in period_results if period.period == "1W")
    one_month_flow = next(period for period in period_results if period.period == "1M")

    assert sample_row["protocol_slug"] == "morpho"
    assert one_week_flow.flow_value == pytest.approx(115_060.93560058785)
    assert one_month_flow.flow_value == pytest.approx(2_345_306.5537264165)
    assert one_week_flow.deposit_value is None
    assert one_week_flow.redeem_value is None
    assert one_month_flow.deposit_value is None
    assert one_month_flow.redeem_value is None
    assert one_week_flow.deposit_count is None
    assert one_week_flow.redemption_count is None

    # The deprecated 7d and 30d records alias the canonical period results.
    legacy_netflow = {flow.period: flow for flow in sample_row["netflow"]}
    assert legacy_netflow["7d"].net_flow_usd == pytest.approx(one_week_flow.flow_value)
    assert legacy_netflow["30d"].net_flow_usd == pytest.approx(one_month_flow.flow_value)
    assert legacy_netflow["7d"].deposit_usd is None
    assert legacy_netflow["7d"].withdrawal_usd is None
    assert legacy_netflow["30d"].deposit_usd is None
    assert legacy_netflow["30d"].withdrawal_usd is None


def test_event_observed_gmx_exports_approximated_daily_metrics(vault_db: VaultDatabase, price_df: pd.DataFrame) -> None:
    """Calculate exact forward-filled risk metrics from sparse GMX events."""

    address = "0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    spec = VaultSpec(43111, address)
    row = vault_db.rows[spec].copy()
    row["Protocol"] = "GMX"
    row["_deposit_closed_reason"] = None
    row["_detection_data"] = replace(
        row["_detection_data"],
        features={ERC4626Feature.gmx_gm, ERC4626Feature.share_price_equivalence},
    )
    gmx_db = VaultDatabase(rows={spec: row})
    gmx_prices = price_df[price_df["id"] == f"43111-{address}"].iloc[:3].copy()
    gmx_prices.index = pd.to_datetime(["2026-01-01", "2026-01-03", "2026-01-06"])
    gmx_prices["share_price"] = [1.0, 1.1, 1.21]
    gmx_prices["total_supply"] = [100.0, 200.0, 150.0]
    gmx_prices["total_assets"] = gmx_prices["share_price"] * gmx_prices["total_supply"]

    result = calculate_lifetime_metrics(gmx_prices, gmx_db).iloc[0]
    three_months = next(period for period in result["period_results"] if period.period == "3M")

    # Forward filling produces returns [0%, 10%, 0%, 0%, 10%]. The supply
    # changes do not affect the supply-normalised price or its return series.
    expected_returns = pd.Series([0.0, 0.1, 0.0, 0.0, 0.1])
    expected_volatility = expected_returns.std() * 365**0.5
    expected_sharpe = expected_returns.mean() / expected_returns.std() * 365**0.5
    assert three_months.volatility == pytest.approx(expected_volatility)
    assert three_months.sharpe == pytest.approx(expected_sharpe)

    for period in result["period_results"]:
        if period.error_reason is not None:
            continue
        available_metrics = (
            period.returns_gross,
            period.returns_net,
            period.cagr_gross,
            period.cagr_net,
            period.volatility,
            period.sharpe,
            period.max_drawdown,
            period.tvl_start,
            period.tvl_end,
            period.tvl_low,
            period.tvl_high,
        )
        assert all(value is not None and pd.notna(value) for value in available_metrics), period.period

    assert pd.notna(result["three_months_volatility"])
    assert pd.notna(result["three_months_sharpe"])


def test_calculate_lifetime_metrics_exports_deposit_permission(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Lifetime reports add scan permission without mutating stored capability data."""
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[vault_spec])
    stored_manager = {"can_deposit": True, "can_redeem": True, "deposit_flow": "synchronous", "redemption_flow": "synchronous"}
    vault_row["_deposit_manager"] = stored_manager
    vault_row["_deposit_permission"] = "whitelisted"
    vault_row["_whitelist_notes"] = "No permissioned hook checks were performed"

    metrics = calculate_lifetime_metrics(price_df.loc[price_df["id"] == vault_id], {vault_spec: vault_row})

    assert metrics.iloc[0]["deposit_manager"] == stored_manager | {"deposit_permission": "whitelisted"}
    assert metrics.iloc[0]["deposit_permission"] == "whitelisted"
    assert metrics.iloc[0]["whitelist"] == {
        "status": "whitelisted",
        "notes": "No permissioned hook checks were performed",
    }
    assert stored_manager == {"can_deposit": True, "can_redeem": True, "deposit_flow": "synchronous", "redemption_flow": "synchronous"}


def test_calculate_lifetime_metrics_exports_withdrawal_period(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Lifetime export preserves withdrawal bounds and availability mechanism."""
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[vault_spec])
    vault_row["_withdrawal_period"] = WithdrawalPeriod(
        min_period=datetime.timedelta(days=1),
        max_period=datetime.timedelta(days=3),
        delay_type=WithdrawalDelayType.delay,
        estimated_settlement=datetime.timedelta(days=2),
    )

    metrics = calculate_lifetime_metrics(price_df.loc[price_df["id"] == vault_id], {vault_spec: vault_row})
    exported = export_lifetime_row(metrics.iloc[0])

    assert exported["lockup"] == 259_200
    assert exported["min_withdrawal_period"] == 86_400
    assert exported["max_withdrawal_period"] == 259_200
    assert exported["withdrawal_delay_type"] == "delay"
    assert exported["estimated_settlement"] == 172_800


def test_calculate_lifetime_metrics_defaults_legacy_deposit_permission_to_unknown(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Old metadata pickles with a manager receive a safe report default."""
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[vault_spec])
    vault_row["_deposit_manager"] = {"can_deposit": True, "can_redeem": True, "deposit_flow": "synchronous", "redemption_flow": "synchronous"}
    vault_row.pop("_deposit_permission", None)

    metrics = calculate_lifetime_metrics(price_df.loc[price_df["id"] == vault_id], {vault_spec: vault_row})

    assert metrics.iloc[0]["deposit_manager"]["deposit_permission"] == "unknown"
    assert metrics.iloc[0]["deposit_permission"] == "unknown"
    assert metrics.iloc[0]["whitelist"] == {"status": "unknown", "notes": None}


def test_calculate_lifetime_metrics_exports_permission_for_refusing_manager(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Permissioned funds retain policy metadata while refusing public flows."""
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[vault_spec])
    vault_row["_deposit_manager"] = {"can_deposit": False, "can_redeem": False}
    vault_row["_deposit_permission"] = "whitelisted"

    metrics = calculate_lifetime_metrics(price_df.loc[price_df["id"] == vault_id], {vault_spec: vault_row})

    assert metrics.iloc[0]["deposit_manager"] == {
        "can_deposit": False,
        "can_redeem": False,
        "deposit_permission": "whitelisted",
    }
    assert metrics.iloc[0]["deposit_permission"] == "whitelisted"
    assert metrics.iloc[0]["whitelist"] == {"status": "whitelisted", "notes": None}


def test_calculate_lifetime_metrics_preserves_null_deposit_manager(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Permission reporting does not fabricate an unsupported manager object."""
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[vault_spec])
    vault_row["_deposit_manager"] = None
    vault_row["_deposit_permission"] = "permissionless"

    metrics = calculate_lifetime_metrics(price_df.loc[price_df["id"] == vault_id], {vault_spec: vault_row})

    assert metrics.iloc[0]["deposit_manager"] is None
    assert metrics.iloc[0]["deposit_permission"] == "permissionless"
    assert metrics.iloc[0]["whitelist"] == {"status": "permissionless", "notes": None}


@pytest.mark.parametrize(
    ("deployment", "expected_slug", "expected_deployment_chain_id"),
    (
        (LIGHTER_ETHEREUM, "ethereum", 1),
        (LIGHTER_ROBINHOOD, "robinhood", 4663),
    ),
)
def test_calculate_lifetime_metrics_exports_lighter_deployment_chain(
    price_df: pd.DataFrame,
    deployment: LighterAPIConfig,
    expected_slug: str,
    expected_deployment_chain_id: int,
) -> None:
    """Keep Lighter dataset identity separate from its associated EVM chain."""
    source_vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    spec, vault_row = create_lighter_pool_row(
        account_index=281474976710654,
        name="Lighter Liquidity Provider (LLP)",
        description="Lighter protocol liquidity and insurance pool.",
        tvl=1_000_000.0,
        created_at=None,
        is_llp=True,
        status=0,
        deployment=deployment,
    )
    vault_prices = price_df.loc[price_df["id"] == source_vault_id].copy()
    vault_prices["id"] = spec.as_string_id()
    vault_prices["chain"] = deployment.chain_id

    metrics = calculate_lifetime_metrics(vault_prices, {spec: vault_row})

    row = metrics.iloc[0]
    # The synthetic ID must remain unchanged because it keys the Lighter price
    # partition; the added fields identify Ethereum versus Robinhood Lighter.
    assert row["chain_id"] == deployment.chain_id
    assert row["deployment"] == expected_slug
    assert row["deployment_chain_id"] == expected_deployment_chain_id

    exported = export_lifetime_row(row)
    assert exported["chain_id"] == deployment.chain_id
    assert exported["deployment"] == expected_slug
    assert exported["deployment_chain_id"] == expected_deployment_chain_id
    assert exported["deposit_permission"] == "permissionless"
    assert exported["whitelist"] == {"status": "permissionless", "notes": None}


def test_calculate_lifetime_metrics_exports_closed_hyperliquid_vault_as_whitelisted(price_df: pd.DataFrame) -> None:
    """Carry Hyperliquid's closed public-deposit status through the JSON export."""
    source_vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    spec, vault_row = create_hyperliquid_vault_row(
        vault_address="0x1111111111111111111111111111111111111111",
        name="Closed Hyperliquid vault",
        description=None,
        tvl=1_000_000.0,
        create_time=None,
        is_closed=True,
    )
    vault_prices = price_df.loc[price_df["id"] == source_vault_id].copy()
    vault_prices["id"] = spec.as_string_id()
    vault_prices["chain"] = HYPERCORE_CHAIN_ID

    metrics = calculate_lifetime_metrics(vault_prices, {spec: vault_row})
    exported = export_lifetime_row(metrics.iloc[0])

    assert exported["deposit_permission"] == "whitelisted"
    assert exported["whitelist"] == {
        "status": "whitelisted",
        "notes": "Vault is permanently closed. Native perp DEX compatibility status: public deposits are unavailable; this does not imply an approved-account deposit route.",
    }


@pytest.mark.parametrize(
    ("bad_flag", "expected_note"),
    [
        (VaultFlag.unofficial, "Vault has bad scan flags: unofficial"),
        (VaultFlag.morpho_issues, "Vault has bad scan flags: morpho_issues"),
    ],
)
def test_calculate_lifetime_metrics_blacklists_scanned_bad_flags(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
    bad_flag: VaultFlag,
    expected_note: str,
):
    """Scanned bad flags override a cached non-blacklisted risk classification."""
    vault_id = "43111-0x614eb485de3c6c49701b40806ac1b985ad6f0a2f"
    spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[spec])
    vault_row["_risk"] = VaultTechnicalRisk.low
    vault_row["_flags"] = {bad_flag}
    vault_rows = {spec: vault_row}
    vault_prices = price_df.loc[price_df["id"] == vault_id]

    metrics = calculate_lifetime_metrics(
        vault_prices,
        vault_rows,
    )

    row = metrics.iloc[0]
    assert row["risk"] == VaultTechnicalRisk.blacklisted
    assert row["risk_numeric"] == VaultTechnicalRisk.blacklisted.value
    assert row["flags"] == {bad_flag}
    assert row["notes"] == expected_note


def test_calculate_lifetime_metrics_nullifies_period_net_returns_when_fee_mode_unknown(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Unknown Securitize fee data never produces implied zero-fee net returns."""
    source_vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_address = "0x671642Ac281C760e34251d51bC9eEF27026F3B7a"
    vault_spec = VaultSpec(5000, vault_address)
    vault_row = dict(vault_db.rows[VaultSpec.parse_string(source_vault_id)])
    vault_row.update(
        {
            "Name": "Mantle Index Four",
            "Address": vault_address,
            "Protocol": "Securitize",
            "_fees": FeeData(
                fee_mode=None,
                management=None,
                performance=None,
                deposit=None,
                withdraw=None,
            ),
        }
    )
    vault_prices = price_df.loc[price_df["id"] == source_vault_id].copy()
    vault_prices["id"] = vault_spec.as_string_id()
    vault_prices["chain"] = 5000

    metrics = calculate_lifetime_metrics(vault_prices, {vault_spec: vault_row})
    row = metrics.iloc[0]

    assert row["fee_mode"] is None
    assert row["cagr_net"] is None
    assert row["lifetime_return_net"] is None
    assert row["one_month_returns_net"] is None
    assert row["three_months_returns_net"] is None

    period_results = row["period_results"]
    assert all(period.returns_net is None for period in period_results)
    assert all(period.cagr_net is None for period in period_results)
    assert any(period.returns_gross is not None for period in period_results)
    assert any(period.cagr_gross is not None for period in period_results)

    exported = export_lifetime_row(row)
    json.dumps(exported)
    assert all(period["returns_net"] is None for period in exported["period_results"])
    assert all(period["cagr_net"] is None for period in exported["period_results"])


def test_calculate_lifetime_metrics_uses_scanned_d2_notes(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """D2 notes scanned from the vault adapter are included in lifetime metric exports."""
    vault_id = "43111-0x614eb485de3c6c49701b40806ac1b985ad6f0a2f"
    address = "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004"
    spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[spec])
    vault_row["Protocol"] = D2_PROTOCOL_NAME
    vault_row["protocol_slug"] = "d2-finance"
    vault_row["Address"] = address
    vault_row["_risk"] = VaultTechnicalRisk.negligible
    vault_row["_flags"] = set()
    vault_row["_notes"] = format_d2_vault_note(address)
    vault_prices = price_df.loc[price_df["id"] == vault_id]

    metrics = calculate_lifetime_metrics(
        vault_prices,
        {spec: vault_row},
    )

    lifetime_row = metrics.iloc[0]
    note = lifetime_row["notes"]
    assert "D2 Finance strategy vault" in note
    assert "**Summary:**" in note
    assert f"[D2 strategy page](https://d2.finance/strategies/{address})" in note

    assert lifetime_row["curator_slug"] == "d2-finance"
    assert lifetime_row["curator_name"] == "D2 Finance"
    assert bool(lifetime_row["protocol_curator"]) is True

    exported = json.loads(json.dumps(export_lifetime_row(lifetime_row)))
    assert exported["protocol"] == "D2 Finance"
    assert exported["protocol_slug"] == "d2-finance"
    assert exported["curator_slug"] == "d2-finance"
    assert exported["curator_name"] == "D2 Finance"
    assert exported["protocol_curator"] is True


def test_calculate_lifetime_metrics_uses_declared_curator_slug(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Carry a reviewed adapter curator declaration into the public export."""

    vault_id = "43111-0x614eb485de3c6c49701b40806ac1b985ad6f0a2f"
    spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[spec])
    vault_row["Name"] = "Unbranded tokenised fund"
    vault_row["Share token"] = "TEST"
    vault_row["Protocol"] = "Tokenisation platform"
    vault_row["protocol_slug"] = "tokenisation-platform"
    vault_row["_manager_name"] = None
    vault_row["_curator_slug"] = "wellington-management"
    vault_prices = price_df.loc[price_df["id"] == vault_id]

    metrics = calculate_lifetime_metrics(vault_prices, {spec: vault_row})

    assert metrics.iloc[0]["curator_slug"] == "wellington-management"
    assert metrics.iloc[0]["curator_name"] == "Wellington Management"


def test_calculate_lifetime_metrics_does_not_apply_non_d2_protocol_notes(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Non-D2 protocol-wide notes are not newly applied in lifetime metric exports."""
    vault_id = "43111-0x614eb485de3c6c49701b40806ac1b985ad6f0a2f"
    spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[spec])
    vault_row["Protocol"] = "Summer.fi"
    vault_row["_risk"] = VaultTechnicalRisk.negligible
    vault_row["_flags"] = set()
    vault_prices = price_df.loc[price_df["id"] == vault_id]

    metrics = calculate_lifetime_metrics(
        vault_prices,
        {spec: vault_row},
    )

    note = metrics.iloc[0]["notes"] or ""
    assert "Summer.fi vault is illiquid" not in note


def test_calculate_period_metrics(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Test period metrics calculation for individual periods.

    - Tests the new structured approach with LOOKBACK_AND_TOLERANCES
    - Tests 1M, 3M, and lifetime periods
    """

    # Use Clearstar vault (has good data quality)
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    vault_spec = VaultSpec.parse_string(vault_id)

    # Extract single vault data
    vault_data = price_df[price_df["id"] == vault_id].copy()
    vault_data = vault_data.sort_index()

    # Get fee data from vault_db (same pattern as calculate_lifetime_metrics)
    vault_row = vault_db.rows[vault_spec]
    fee_data = vault_row.get("_fees")
    if fee_data is None:
        # Legacy fallback
        fee_data = FeeData(
            fee_mode=VaultFeeMode.externalised,
            management=vault_row["Mgmt fee"],
            performance=vault_row["Perf fee"],
            deposit=vault_row.get("Deposit fee", 0),
            withdraw=vault_row.get("Withdrawal fee", 0),
        )
    net_fee_data = fee_data.get_net_fees()

    # Prepare inputs
    share_price_hourly = vault_data["share_price"]
    share_price_daily, daily_returns = prepare_daily_share_price_series(share_price_hourly)
    tvl = vault_data["total_assets"]
    now_ = vault_data.index.max()

    # Test 1M period
    metrics_1m = calculate_period_metrics(
        period="1M",
        gross_fee_data=fee_data,
        net_fee_data=net_fee_data,
        share_price_hourly=share_price_hourly,
        share_price_daily=share_price_daily,
        daily_returns=daily_returns,
        tvl=tvl,
        now_=now_,
    )
    assert metrics_1m.period == "1M"
    assert metrics_1m.error_reason is None, f"1M period failed: {metrics_1m.error_reason}"
    assert metrics_1m.raw_samples > 0
    # Compare with existing test values from test_calculate_lifetime_metrics
    assert metrics_1m.returns_gross == pytest.approx(0.0018523254977500514, rel=0.01)
    assert metrics_1m.tvl_end > 0

    # Test 3M period (may have sparse data issue based on test data)
    metrics_3m = calculate_period_metrics(
        period="3M",
        gross_fee_data=fee_data,
        net_fee_data=net_fee_data,
        share_price_hourly=share_price_hourly,
        share_price_daily=share_price_daily,
        daily_returns=daily_returns,
        tvl=tvl,
        now_=now_,
    )
    assert metrics_3m.period == "3M"
    # 3M may have error due to sparse data in test dataset
    # The test data spans ~41 days, so 3M lookback will use all available data

    # Test lifetime period
    metrics_lifetime = calculate_period_metrics(
        period="lifetime",
        gross_fee_data=fee_data,
        net_fee_data=net_fee_data,
        share_price_hourly=share_price_hourly,
        share_price_daily=share_price_daily,
        daily_returns=daily_returns,
        tvl=tvl,
        now_=now_,
    )
    assert metrics_lifetime.period == "lifetime"
    assert metrics_lifetime.error_reason is None, f"Lifetime period failed: {metrics_lifetime.error_reason}"
    # Compare with existing test values from test_calculate_lifetime_metrics
    assert metrics_lifetime.returns_gross == pytest.approx(0.002758, rel=0.01)
    assert metrics_lifetime.raw_samples > 0
    assert metrics_lifetime.daily_samples > 0
    assert metrics_lifetime.tvl_start > 0
    assert metrics_lifetime.tvl_end > 0

    # When no utilisation series is provided, avg_utilisation must be None for all periods
    assert metrics_1m.avg_utilisation is None
    assert metrics_lifetime.avg_utilisation is None

    # Test avg_utilisation with a synthetic utilisation series (lending vault scenario).
    # All samples are 0.75 so the mean must equal 0.75 exactly.
    utilisation_uniform = pd.Series(0.75, index=vault_data.index).resample("D").last().ffill()
    metrics_util_lifetime = calculate_period_metrics(
        period="lifetime",
        gross_fee_data=fee_data,
        net_fee_data=net_fee_data,
        share_price_hourly=share_price_hourly,
        share_price_daily=share_price_daily,
        daily_returns=daily_returns,
        tvl=tvl,
        now_=now_,
        utilisation=utilisation_uniform,
    )
    assert metrics_util_lifetime.avg_utilisation == pytest.approx(0.75)

    # Test that varying utilisation values are averaged correctly.
    # Assign linearly spaced values 0.6 … 0.8 across the vault index; the mean is 0.7.
    n = len(vault_data)
    utilisation_varying = (
        pd.Series(
            [0.6 + 0.2 * i / (n - 1) for i in range(n)],
            index=vault_data.index,
        )
        .resample("D")
        .last()
        .ffill()
    )
    metrics_util_varying = calculate_period_metrics(
        period="lifetime",
        gross_fee_data=fee_data,
        net_fee_data=net_fee_data,
        share_price_hourly=share_price_hourly,
        share_price_daily=share_price_daily,
        daily_returns=daily_returns,
        tvl=tvl,
        now_=now_,
        utilisation=utilisation_varying,
    )
    assert metrics_util_varying.avg_utilisation == pytest.approx(utilisation_varying.mean())


def test_apply_abnormal_value_checks_handles_pandas_na():
    """Regression test for pandas.NA values in numeric comparisons."""

    risk, notes, flags = apply_abnormal_value_checks(
        risk=VaultTechnicalRisk.negligible,
        notes="",
        flags=set(),
        current_nav=pd.NA,
        current_share_price=pd.NA,
        three_months_volatility=pd.NA,
    )

    assert risk == VaultTechnicalRisk.negligible
    assert notes == ""
    assert flags == set()

    risk, notes, flags = apply_abnormal_value_checks(
        risk=VaultTechnicalRisk.negligible,
        notes="",
        flags=set(),
        current_nav=100_000_000_001,
        current_share_price=1_000_001,
        three_months_volatility=10_001,
    )

    assert risk == VaultTechnicalRisk.blacklisted
    assert notes
    assert VaultFlag.abnormal_tvl in flags
    assert VaultFlag.abnormal_share_price in flags
    assert VaultFlag.abnormal_volatility in flags


def test_vault_charts(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Draw vault chart figures."""

    spec = VaultSpec.parse_string("43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883")
    display_vault_chart_and_tearsheet(
        spec,
        prices_df=price_df,
        vault_db=vault_db,
        render=False,
    )


def test_render_vault_sparkline(
    price_df: pd.DataFrame,
):
    """Render spark line chart."""

    spec = VaultSpec.parse_string("43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883")
    vault_prices_df = extract_vault_price_data(spec, price_df)
    fig = render_sparkline_simple(
        vault_prices_df,
        width=128,
        height=32,
    )
    png_data = export_sparkline_as_png(
        fig,
    )
    assert type(png_data) == bytes

    svg_data = export_sparkline_as_svg(
        fig,
    )
    assert type(svg_data) == bytes


@pytest.mark.skipif(os.environ.get("R2_SPARKLINE_BUCKET_NAME") is None, reason="R2_SPARKLINE_BUCKET_NAME not set")
def test_upload_vault_sparkline(
    price_df: pd.DataFrame,
):
    """Render spark line chart."""

    spec = VaultSpec.parse_string("43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883")
    vault_prices_df = extract_vault_price_data(spec, price_df)
    fig = render_sparkline_simple(
        vault_prices_df,
        width=128,
        height=32,
    )
    png_data = export_sparkline_as_png(fig)
    assert type(png_data) == bytes

    object_name = f"test-{spec.as_string_id()}.png"
    bucket_name = os.environ.get("R2_SPARKLINE_BUCKET_NAME")
    access_key_id = os.environ.get("R2_SPARKLINE_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SPARKLINE_SECRET_ACCESS_KEY")
    endpoint_url = os.environ.get("R2_SPARKLINE_ENDPOINT_URL")

    from eth_defi.research.sparkline import upload_to_r2_compressed

    upload_to_r2_compressed(
        payload=png_data,
        bucket_name=bucket_name,
        object_name=object_name,
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        content_type="image/png",
    )


def test_vault_benchmark(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Draw the vault chart benchmark chart.

    - Only 1 vault to benchmark
    """

    spec = VaultSpec.parse_string("43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883")
    fig, df = visualise_vault_return_benchmark(
        [spec],
        prices_df=price_df,
        vault_db=vault_db,
    )
    assert isinstance(fig, Figure)
    assert isinstance(df, pd.DataFrame)


def test_export_lifetime_metrics(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Export lifetime metrics for the frontend"""

    metrics = calculate_lifetime_metrics(
        price_df,
        vault_db,
    )
    rows = [export_lifetime_row(r) for _, r in metrics.iterrows()]
    # Ensure everything is JSON serializable
    json.dumps(rows)

    r = rows[0]
    assert r["name"] == "Clearstar USDC.e"
    assert r["chain"] == "Hemi"

    # Lending statistics fields should be present in exported data
    # Values may be None if not available in test data
    assert "available_liquidity" in r
    assert "utilisation" in r
    assert "vault_poll_frequency" in r
    # Verify they serialize to JSON properly (None becomes null)
    assert r["available_liquidity"] is None or isinstance(r["available_liquidity"], (int, float))
    assert r["utilisation"] is None or isinstance(r["utilisation"], (int, float))


def test_calculate_lifetime_metrics_exports_share_price_source(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Adapter price-source metadata reaches the DataFrame and JSON export."""

    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[spec])
    vault_row["_share_price_source"] = PriceSource.smart_contract_state

    metrics = calculate_lifetime_metrics(
        price_df.loc[price_df["id"] == vault_id],
        {spec: vault_row},
    )

    assert metrics.iloc[0]["share_price_source"] == "smart-contract-state"
    assert export_lifetime_row(metrics.iloc[0])["share_price_source"] == "smart-contract-state"


def test_calculate_lifetime_metrics_exports_strategy_tags_to_json(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Persisted strategy tags survive lifetime calculation and JSON export."""

    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[spec])
    vault_row["_strategy_tags"] = {StrategyTag.lending, StrategyTag.algorithmic_trading}

    metrics = calculate_lifetime_metrics(
        price_df.loc[price_df["id"] == vault_id],
        {spec: vault_row},
    )

    assert metrics.iloc[0]["strategy_tags"] == ["algorithmic_trading", "lending"]
    exported = export_lifetime_row(metrics.iloc[0])
    encoded = json.dumps(exported)
    decoded = json.loads(encoded)

    assert decoded["strategy_tags"] == ["algorithmic_trading", "lending"]


def test_calculate_lifetime_metrics_exports_vault_minimums(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
) -> None:
    """Export known, absent, and zero vault minimums without conflating them.

    1. Add a known deposit minimum and a confirmed zero redemption minimum to scanned metadata.
    2. Calculate and JSON-export the vault metrics.
    3. Remove the metadata fields and verify legacy unavailable values remain null.
    """
    vault_id = "43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"
    spec = VaultSpec.parse_string(vault_id)
    vault_row = dict(vault_db.rows[spec])
    vault_row["_minimum_deposit"] = Decimal("12.5")
    vault_row["_minimum_redemption"] = Decimal(0)
    vault_prices = price_df.loc[price_df["id"] == vault_id]

    # 1. Calculate metadata with source-proven values.
    metrics = calculate_lifetime_metrics(vault_prices, {spec: vault_row})

    # 2. Preserve decimal units in metrics and strict JSON values in the export.
    assert metrics.iloc[0]["minimum_deposit"] == Decimal("12.5")
    assert metrics.iloc[0]["minimum_redemption"] == Decimal(0)
    exported = export_lifetime_row(metrics.iloc[0])
    assert exported["minimum_deposit"] == 12.5
    assert exported["minimum_redemption"] == 0.0

    # 3. Preserve unavailable legacy metadata as null, not as no minimum.
    vault_row.pop("_minimum_deposit")
    vault_row.pop("_minimum_redemption")
    legacy_metrics = calculate_lifetime_metrics(vault_prices, {spec: vault_row})
    assert legacy_metrics.iloc[0]["minimum_deposit"] is None
    assert legacy_metrics.iloc[0]["minimum_redemption"] is None


def test_export_lifetime_row_nat_serialization():
    """Test that NaT values are properly serialized as None/null, not the string "NaT".

    This is a regression test for a bug where pd.NaT values were being converted
    to the string "NaT" instead of null in JSON output.
    """
    # Create a test DataFrame with NaT values in various columns
    # We need to explicitly set dtypes to force pandas to convert None to NaT
    test_data = {
        "name": ["Test Vault"],
        "chain": ["test-chain"],
        "current_nav": [1000.0],
        "lockup": [None],
        "one_month_start": [None],
        "one_month_end": [None],
        "three_months_start": [None],
        "three_months_end": [None],
        "cagr": [0.05],
        "event_count": [100],
    }

    df = pd.DataFrame(test_data)
    # Force datetime columns to datetime64[ns] dtype, which converts None to NaT
    df["one_month_start"] = pd.to_datetime(df["one_month_start"])
    df["one_month_end"] = pd.to_datetime(df["one_month_end"])
    df["three_months_start"] = pd.to_datetime(df["three_months_start"])
    df["three_months_end"] = pd.to_datetime(df["three_months_end"])
    # Force lockup to float, which also converts None to NaT in this context
    df["lockup"] = df["lockup"].astype("float64")

    row = df.iloc[0]

    # Verify that pandas has converted None to NaT for datetime fields
    # (this is the precondition that caused the bug)
    row_dict = row.to_dict()
    assert row_dict["one_month_start"] is pd.NaT
    # For numeric columns, None becomes NaN which also gets represented as NaT in to_dict()
    assert pd.isna(row_dict["lockup"])

    # Export the row
    result = export_lifetime_row(row)

    # Verify the result is JSON serializable
    json_str = json.dumps(result)

    # Parse it back to verify the actual values
    parsed = json.loads(json_str)

    # These fields should be null in JSON, NOT the string "NaT"
    assert parsed["lockup"] is None, f"lockup should be null, got {parsed['lockup']!r}"
    assert parsed["one_month_start"] is None, f"one_month_start should be null, got {parsed['one_month_start']!r}"
    assert parsed["one_month_end"] is None, f"one_month_end should be null, got {parsed['one_month_end']!r}"
    assert parsed["three_months_start"] is None, f"three_months_start should be null, got {parsed['three_months_start']!r}"
    assert parsed["three_months_end"] is None, f"three_months_end should be null, got {parsed['three_months_end']!r}"

    # Verify that the JSON string does not contain the literal string "NaT"
    assert '"NaT"' not in json_str, f"JSON output should not contain the string 'NaT', but got: {json_str}"

    # Verify other fields are still properly serialized
    assert parsed["name"] == "Test Vault"
    assert parsed["current_nav"] == 1000.0


def test_export_lifetime_row_preserves_flow_fees_without_annual_fees() -> None:
    """Export deposit and withdrawal fees even when annual fees are unknown."""

    row = pd.Series(
        {
            "name": "Test Vault",
            "mgmt_fee": None,
            "perf_fee": None,
            "deposit_fee": 0.01,
            "withdraw_fee": 0.02,
        }
    )

    result = export_lifetime_row(row)

    assert result["management_fee"] is None
    assert result["performance_fee"] is None
    assert result["deposit_fee"] == 0.01
    assert result["withdraw_fee"] == 0.02


def test_export_lifetime_row_converts_nested_decimals_to_strict_json_floats() -> None:
    """Export Decimal-based vault metadata as strict JSON numeric values."""
    row = pd.Series(
        {
            "deposit_fee": Decimal("0.015"),
            "withdraw_fee": Decimal("NaN"),
            "deposit_manager": {
                "minimum_deposit": Decimal("12.50"),
                "fee_tiers": [Decimal("0.001"), Decimal("Infinity")],
            },
        }
    )

    result = export_lifetime_row(row)

    assert result["deposit_fee"] == pytest.approx(0.015)
    assert isinstance(result["deposit_fee"], float)
    assert result["withdraw_fee"] is None
    assert result["deposit_manager"] == {
        "minimum_deposit": 12.5,
        "fee_tiers": [0.001, None],
    }
    json.dumps(result, allow_nan=False)

"""Test vault metrics calculations and charts."""

import json
import os.path
import pickle
from pathlib import Path

import pandas as pd
import pytest
import zstandard as zstd
from plotly.graph_objects import Figure

from eth_defi.research.sparkline import export_sparkline_as_png, export_sparkline_as_svg, extract_vault_price_data, render_sparkline_simple
from eth_defi.research.vault_benchmark import visualise_vault_return_benchmark
from eth_defi.research.vault_metrics import PeriodMetrics, calculate_lifetime_metrics, calculate_period_metrics, display_vault_chart_and_tearsheet, export_lifetime_row, format_lifetime_table
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.vaultdb import VaultDatabase


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

    assert sample_row["lifetime_return"] == pytest.approx(0.002758)
    assert sample_row["cagr"] == pytest.approx(0.02483940718068034)
    assert sample_row["cagr_net"] == pytest.approx(0.02483940718068034)

    # Three months metrics - the test data spans ~41 days, which fits within 3M tolerance
    assert sample_row["three_months_cagr"] == pytest.approx(0.02483940718068034)
    assert sample_row["three_months_cagr_net"] == pytest.approx(0.02483940718068034)
    assert sample_row["three_months_sharpe"] == pytest.approx(5.280916994701033)
    assert sample_row["three_months_sharpe_net"] == pytest.approx(5.280916994701033)

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

    # Verify period_results is not in formatted output
    # assert "period_results" not in formatted.columns


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
    share_price_daily = share_price_hourly.resample("D").last().dropna()
    tvl = vault_data["total_assets"]
    now_ = vault_data.index.max()

    # Test 1M period
    metrics_1m = calculate_period_metrics(
        period="1M",
        gross_fee_data=fee_data,
        net_fee_data=net_fee_data,
        share_price_hourly=share_price_hourly,
        share_price_daily=share_price_daily,
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
    vault_db: VaultDatabase,
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
    vault_db: VaultDatabase,
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
    account_id = os.environ.get("R2_SPARKLINE_ACCOUNT_ID")
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
    # Verify they serialize to JSON properly (None becomes null)
    assert r["available_liquidity"] is None or isinstance(r["available_liquidity"], (int, float))
    assert r["utilisation"] is None or isinstance(r["utilisation"], (int, float))


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

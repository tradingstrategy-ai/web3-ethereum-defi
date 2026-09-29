"""Unit tests for the monthly vault report pipeline, using synthetic data."""

import base64
import datetime
import hashlib
import hmac
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest
import requests
from PIL import Image, ImageDraw

from eth_defi.research.vault_correlation import choose_vaults_for_correlation_comparison
from eth_defi.research.vault_metrics import calculate_sharpe_ratio_from_returns
from eth_defi.vault_report import report as report_module
from eth_defi.vault_report import vault_checks as vault_checks_module
from eth_defi.vault_report import vault_probes as vault_probes_module
from eth_defi.vault_report.benchmarks import BTC, ETH, TREASURY_BILL, calculate_treasury_bill_index, select_benchmarks
from eth_defi.vault_report.branding import CHART_SCALE, HERO_SIZE, PANEL_PADDING, PANEL_WIDTH, SQUARE_HERO_SIZE, compose_chart_panel
from eth_defi.vault_report.charts import CHOREOGRAPHER_CHROME_PATH, LEGEND_MARGIN, LegendEntry, PerformanceSeries, VaultProperty, add_logo_legend, calculate_period_performance, calculate_rolling_sharpe, create_performance_figure, create_risk_return_figure, plain_text, select_moving_vaults, trim_logos, wrap_label
from eth_defi.vault_report.data import VaultReportData, calculate_daily_share_prices, prepare_vault_metrics, read_vault_share_prices, read_vault_tvl_history
from eth_defi.vault_report.ghost import GhostAdminClient, GhostAPIError, GhostContentClient, GhostPost, create_ghost_admin_token
from eth_defi.vault_report.logos import load_benchmark_logo_uri
from eth_defi.vault_report.podcasts import parse_podcast_episode, render_podcast_episodes
from eth_defi.vault_report.post import extract_section_html, make_report_slug, read_changelog_entries
from eth_defi.vault_report.report import collect_top_lists, generate_monthly_vault_report, make_vault_properties, publish_report_draft
from eth_defi.vault_report.sections import AMM, LENDING, OTHER, OTHER_PROTOCOL, PERP_DEX, RWA, TOKENISED_FUND, ReportCriteria, ReportSection, calculate_average_yields, calculate_chain_tvl_history, calculate_chain_yields, calculate_fund_nav_history, calculate_high_yield_protocols, calculate_protocol_tvl_history, calculate_protocol_yields, calculate_tvl_changes, classify_vault, exclude_amm_pools, exclude_chart_risks, filter_eligible_vaults, format_return, format_sharpe, format_vault_cells, is_identified_protocol, render_section_table, select_average_yield_vaults, select_comparable_vaults, select_group, select_vaults_by_chain, select_yield_vaults
from eth_defi.vault_report.theme import DARK_THEME
from eth_defi.vault_report.vault_checks import RULES_VERSION, SCHEMA_VERSION, SCOPE_VERSION, CheckCandidate, CheckDecision, CheckValidationError, VaultCheckSettings, build_agent_command, build_check_candidates, candidate_depth, check_blacklist_entries, read_check_decisions, run_check_agent, write_candidates_file
from eth_defi.vault_report.vault_probes import Exposure, VaultFacts, raise_signals, select_probe

DATA_END_AT = datetime.datetime(2026, 9, 24)


def make_vault_record(address: str, **overrides) -> dict:
    """Create a synthetic top vaults JSON record.

    :param address:
        Vault address, also used as the name.

    :return:
        Record with the fields the report reads.
    """
    record = {
        "id": f"1-{address}",
        "address": address,
        "name": f"Vault {address}",
        "chain": "Ethereum",
        "chain_id": 1,
        "protocol": "Morpho",
        "protocol_slug": "morpho",
        "denomination": "USDC",
        "normalised_denomination": "USDC",
        "one_month_cagr": 0.10,
        "one_month_cagr_net": 0.08,
        "one_month_returns": 0.008,
        "three_months_cagr": 0.09,
        "three_months_cagr_net": None,
        "three_months_returns": 0.02,
        "cagr": 0.07,
        "cagr_net": None,
        "three_months_sharpe": 3.0,
        "three_months_sharpe_net": None,
        "three_months_volatility": 0.001,
        "current_nav": 1_000_000.0,
        "peak_nav": 2_000_000.0,
        "years": 1.5,
        "event_count": 100,
        "risk": "Low",
        "flags": [],
        "start_date": "2025-03-01T00:00:00",
        "end_date": DATA_END_AT.isoformat(),
        "trading_strategy_link": f"https://tradingstrategy.ai/vaults/{address}",
        "vault_slug": f"vault-{address}",
        "strategy_tags": ["lending"],
        "period_results": [{"period": "1M", "tvl_start": 1_000_000.0, "tvl_end": 1_000_000.0}, {"period": "3M", "max_drawdown": 0.0}],
    }
    record.update(overrides)
    # The export carries the numeric risk level next to its label, see VaultTechnicalRisk
    record.setdefault("risk_numeric", {"Negligible": 1, "Low": 20, "High": 30, "Severe": 40, "Dangerous": 50, "Blacklisted": 999}.get(record["risk"]))
    return record


@pytest.fixture()
def vault_records() -> list[dict]:
    """Synthetic vaults covering each filter rule."""
    return [
        make_vault_record("0xaa", one_month_cagr_net=0.20, three_months_cagr=0.18),
        make_vault_record("0xbb", one_month_cagr_net=None, one_month_cagr=0.15),
        make_vault_record("0xcc", one_month_cagr_net=0.50, risk="Blacklisted"),
        make_vault_record("0xdd", one_month_cagr_net=0.60, end_date="2026-08-01T00:00:00"),
        make_vault_record("0xee", one_month_cagr_net=0.90, current_nav=50_000.0),
        make_vault_record("0xff", chain="Hypercore", protocol="Hyperliquid", protocol_slug="hyperliquid", one_month_cagr_net=100.0, one_month_returns=0.9, three_months_returns=1.0, event_count=2, flags=["perp_dex_trading_vault"], three_months_volatility=0.8),
        make_vault_record("0x11", chain="Hypercore", protocol="Hyperliquid", protocol_slug="hyperliquid", one_month_cagr_net=100.0, one_month_returns=1.5, three_months_returns=2.0, event_count=2, flags=["perp_dex_trading_vault"], three_months_volatility=0.8),
        make_vault_record("0x22", chain="Base", one_month_cagr_net=0.05, current_nav=3_000_000.0, years=0.1, period_results=[{"period": "1M", "tvl_start": 1_000_000.0, "tvl_end": 3_000_000.0}]),
        make_vault_record("0x33", protocol="ERC-4626", protocol_slug="erc-4626", strategy_tags=None, one_month_cagr_net=0.30),
        make_vault_record("0x55", protocol="Yearn", protocol_slug="yearn", strategy_tags=None, one_month_cagr_net=0.12),
        make_vault_record("0x44", protocol="Securitize", protocol_slug="securitize", strategy_tags=None, flags=["tokenised_fund"], one_month_cagr_net=0.045, event_count=2, period_results=[{"period": "1M", "tvl_start": 2_000_000.0, "tvl_end": 1_000_000.0}]),
    ]


@pytest.fixture()
def vaults_df(vault_records: list[dict]) -> pd.DataFrame:
    """Vault metrics DataFrame of the synthetic vaults."""
    return prepare_vault_metrics(vault_records)


@pytest.fixture()
def prices_path(tmp_path: Path, vault_records: list[dict]) -> Path:
    """Hourly share prices, stored with timestamp as the pandas index like the production file."""
    timestamps = pd.date_range(DATA_END_AT - datetime.timedelta(days=200), DATA_END_AT, freq="6h")
    frames = [pd.DataFrame({"timestamp": timestamps, "id": record["id"], "share_price": 1.0 + 0.0002 * i * pd.RangeIndex(len(timestamps)), "total_assets": 1_000_000.0 * i}) for i, record in enumerate(vault_records, start=1)]
    df = pd.concat(frames).set_index("timestamp")
    path = tmp_path / "prices.parquet"
    df.to_parquet(path)
    return path


@pytest.fixture(autouse=True)
def offline_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace network reads of the report pipeline with synthetic data.

    Real providers are covered by ``test_vault_report_live.py``.
    """
    yields = pd.Series(0.04, index=pd.date_range(DATA_END_AT - datetime.timedelta(days=400), DATA_END_AT, freq="D"))
    monkeypatch.setattr(report_module, "fetch_treasury_bill_yields", lambda cache_dir: yields)
    days = pd.date_range(DATA_END_AT - datetime.timedelta(days=200), DATA_END_AT, freq="D")
    crypto = {BTC: pd.Series(range(100, 100 + len(days)), index=days, dtype=float), ETH: pd.Series(range(200, 200 - len(days), -1), index=days, dtype=float)}
    monkeypatch.setattr(report_module, "fetch_benchmark_indices", lambda start_at, end_at, cache_dir, treasury_yields: {TREASURY_BILL: calculate_treasury_bill_index(treasury_yields, end_at), **crypto})
    monkeypatch.setattr(report_module, "fetch_chain_logo_uri", lambda chain, cache_dir: None)
    monkeypatch.setattr(report_module, "fetch_available_sparklines", lambda vault_ids: set(list(vault_ids)[:1]))


def test_filter_and_group_sections(vaults_df: pd.DataFrame):
    """Vaults are grouped, filtered by TVL and activity, and ranked by return or Sharpe."""
    criteria = ReportCriteria()
    eligible = filter_eligible_vaults(vaults_df, DATA_END_AT, criteria)
    assert set(eligible["address"]) == {"0xaa", "0xbb", "0xee", "0xff", "0x11", "0x22", "0x33", "0x44", "0x55"}
    assert eligible["group"].to_dict() == {"1-0xaa": LENDING, "1-0xbb": LENDING, "1-0xee": LENDING, "1-0xff": PERP_DEX, "1-0x11": PERP_DEX, "1-0x22": LENDING, "1-0x33": OTHER, "1-0x44": TOKENISED_FUND, "1-0x55": OTHER}

    # 0xee is below the $100k TVL threshold
    assert list(select_group(eligible, criteria, LENDING)["address"]) == ["0xaa", "0xbb", "0x22"]
    # Tied capped annualised returns are ranked by the absolute monthly return; perp vaults need no deposit events
    assert list(select_group(eligible, criteria, PERP_DEX)["address"]) == ["0x11", "0xff"]
    assert list(select_group(eligible, criteria, PERP_DEX, by="three_months_sharpe_best")["address"]) == ["0x11", "0xff"]
    assert list(select_group(eligible, criteria, OTHER)["address"]) == ["0x33", "0x55"]
    # Tokenised funds need no deposit events either
    assert list(select_group(eligible, criteria, TOKENISED_FUND)["address"]) == ["0x44"]
    assert set(select_yield_vaults(eligible, criteria)["address"]) == {"0xaa", "0xbb", "0x22", "0x33", "0x44", "0x55"}

    # Performance comparisons leave out vaults without an identified protocol, here the generic ERC-4626 vault
    comparable = select_comparable_vaults(eligible)
    assert set(comparable["address"]) == set(eligible["address"]) - {"0x33"}
    assert list(select_group(comparable, criteria, OTHER)["address"]) == ["0x55"]

    by_chain = select_vaults_by_chain(eligible, ReportCriteria(chain_top_n=1))
    assert list(by_chain["address"]) == ["0x22", "0x33", "0x11"]


def test_amm_pools(vault_records: list[dict]):
    """GMX and Curve AMM pools, identified by the amm_pool_like feature, get their own section with a $1M TVL minimum and are left out of other rankings by default."""
    records = [
        *vault_records,
        make_vault_record("0x66", chain="Arbitrum", protocol="GMX", protocol_slug="gmx", strategy_tags=None, features=["amm_pool_like", "gmx_gm"], one_month_cagr_net=0.9, three_months_cagr=2.4, current_nav=5_000_000.0),
        make_vault_record("0x77", protocol="YieldBasis", protocol_slug="yieldbasis", strategy_tags=["amm"], features=["amm_pool_like", "yield_basis_lt"], one_month_cagr_net=0.5, current_nav=500_000.0),
        # AMM-style strategy tags without the AMM pool feature do not make a vault an AMM pool
        make_vault_record("0x99", protocol="Gains Network", protocol_slug="gains-network", strategy_tags=["amm", "market_making_amm"]),
        make_vault_record("0x88", chain="Hypercore", protocol="Hyperliquid", protocol_slug="hyperliquid", strategy_tags=["liquidity_provider"], flags=["perp_dex_trading_vault"], event_count=2),
    ]
    criteria = ReportCriteria()
    comparable = select_comparable_vaults(filter_eligible_vaults(prepare_vault_metrics(records), DATA_END_AT, criteria))
    assert comparable.loc[["1-0x66", "1-0x77", "1-0x88", "1-0x99"], "group"].tolist() == [AMM, AMM, PERP_DEX, OTHER]

    # The AMM table needs $1M TVL, so the $500k YieldBasis pool is left out
    assert list(select_group(comparable, criteria, AMM)["address"]) == ["0x66"]

    # AMM pools are left out of the other rankings, unless included
    ranked = exclude_amm_pools(comparable, criteria)
    assert "1-0x66" not in select_yield_vaults(ranked, criteria).index
    assert "1-0x66" not in select_average_yield_vaults(ranked, criteria).index
    assert "1-0x66" in select_yield_vaults(exclude_amm_pools(comparable, ReportCriteria(include_amm_pools=True)), criteria).index


def test_average_yields(vaults_df: pd.DataFrame):
    """Average yields are TVL-weighted, exclude outliers and volatile vaults, and leave out placeholder protocols."""
    criteria = ReportCriteria()
    eligible = filter_eligible_vaults(vaults_df, DATA_END_AT, criteria)
    yield_vaults = select_average_yield_vaults(select_comparable_vaults(eligible), criteria)
    assert "1-0xff" not in yield_vaults.index  # 80% volatility
    assert "1-0x33" not in yield_vaults.index  # Generic ERC-4626 vault without an identified protocol

    by_chain = calculate_average_yields(yield_vaults, "chain")
    # Averages use the annualised three-month return. Ethereum: 0.18 @ $1M, then 0.09 @ $1M, $50k, $1M and $1M
    assert by_chain.loc["Ethereum", "avg_return"] == pytest.approx((0.18 + 0.09 * 3 + 0.09 * 0.05) / 4.05)
    assert list(calculate_chain_yields(yield_vaults, ReportCriteria(yield_top_chains=1)).index) == ["Ethereum"]

    protocols = calculate_protocol_yields(yield_vaults, criteria)
    assert "ERC-4626" not in protocols.index
    assert set(protocols.index) == {"Morpho", "Securitize", "Yearn"}

    # The high yield chart ranks protocols with at least $150k TVL by average yield
    high_yield = calculate_high_yield_protocols(yield_vaults, ReportCriteria(yield_top_protocols=2))
    assert list(high_yield["avg_return"]) == sorted(high_yield["avg_return"], reverse=True)
    assert len(high_yield) == 2
    assert calculate_high_yield_protocols(yield_vaults, ReportCriteria(yield_high_yield_min_protocol_tvl=10e9)).empty


def test_tvl_changes(vaults_df: pd.DataFrame):
    """TVL changes come from the one-month period, the largest increases first."""
    eligible = filter_eligible_vaults(vaults_df, DATA_END_AT, ReportCriteria())
    changes = calculate_tvl_changes(eligible, ReportCriteria(tvl_change_top_n=1))
    assert list(changes.index) == ["1-0x22", "1-0x44"]
    assert list(changes["tvl_change"]) == [2_000_000.0, -1_000_000.0]


def test_formatting():
    """Net returns are preferred and extreme Sharpe ratios are capped."""
    assert format_return(0.1234, 0.2) == "12.3% (n)"
    assert format_return(None, 0.0) == "0.0% (g)"
    assert format_return(None, None) == "---"
    assert format_return(100.0, None) == ">9,999% (n)"
    assert format_return(99.0, None) == "9,900.0% (n)"
    assert format_return(99.995, None) == "9,999.5% (n)"  # Only the export's 10,000% cap is shown as capped
    assert format_sharpe(8_205_524.0) == ">100"
    assert format_sharpe(float("nan")) == "---"


def test_daily_prices_and_performance(prices_path: Path):
    """Share prices are read without the pandas index, and performance starts from the window start."""
    prices = read_vault_share_prices(prices_path, ["1-0xaa", "1-0xbb"])
    assert set(prices["id"]) == {"1-0xaa", "1-0xbb"}
    daily = calculate_daily_share_prices(prices)
    assert list(daily.columns) == ["1-0xaa", "1-0xbb"]

    start = daily.index[-1] - pd.Timedelta(days=90)
    performance = calculate_period_performance(daily["1-0xaa"], start)
    assert performance.iloc[0] == 0
    assert performance.iloc[-1] == pytest.approx((daily["1-0xaa"].iloc[-1] / daily.loc[start, "1-0xaa"] - 1) * 100)

    # All vaults share one chart: a casing, a line and an end dot per vault, and each available benchmark once
    indices = {TREASURY_BILL: calculate_treasury_bill_index(pd.Series(0.04, index=daily.index), daily.index[-1])}
    series = [PerformanceSeries("1-0xaa", "A", (VaultProperty("Morpho"), VaultProperty("Ethereum")), (TREASURY_BILL,)), PerformanceSeries("1-0xbb", "B", (), (BTC, ETH, TREASURY_BILL))]
    fig = create_performance_figure(series, daily, indices, DARK_THEME)
    assert [trace.name for trace in fig.data if trace.mode == "lines" and trace.name] == ["A", "B", TREASURY_BILL]
    assert fig.layout.yaxis.type != "log"

    # Benchmark logos are drawn in the legend and at the line end
    logos = {TREASURY_BILL: load_benchmark_logo_uri(TREASURY_BILL), BTC: load_benchmark_logo_uri(BTC), ETH: load_benchmark_logo_uri(ETH)}
    assert all(uri.startswith("data:image/svg+xml;base64,") for uri in logos.values())
    assert load_benchmark_logo_uri("Unknown") is None
    fig = create_performance_figure(series, daily, indices, DARK_THEME, benchmark_logos=logos)
    assert sum(image.source == logos[TREASURY_BILL] for image in fig.layout.images) == 2

    # Legends and line end badges show the table rank, which can skip vaults left out of the chart
    fig = create_performance_figure([PerformanceSeries("1-0xaa", "A", (), (TREASURY_BILL,), rank=3)], daily, indices, DARK_THEME)
    assert any(annotation.text.startswith("<b>3. A") for annotation in fig.layout.annotations)  # Bold vault names

    # A vault returning more than the threshold switches the shared axis to a log scale
    fig = create_performance_figure(series, daily, indices, DARK_THEME, log_threshold=-100)
    assert fig.layout.yaxis.type == "log"

    # One vault far above the others is drawn off scale, and the axis fits the rest
    calm = pd.Series(np.linspace(100, 101, len(daily)), index=daily.index)
    spiky = pd.Series(np.linspace(100, 1_000, len(daily)), index=daily.index)
    outlier_prices = pd.DataFrame({"calm1": calm, "calm2": calm * 1.001, "spiky": spiky})
    outlier_series = [PerformanceSeries(vault_id, vault_id, (), (TREASURY_BILL,)) for vault_id in outlier_prices.columns]
    fig = create_performance_figure(outlier_series, outlier_prices, indices, DARK_THEME)
    assert fig.layout.yaxis.type != "log"
    assert fig.layout.yaxis.range[1] < 110  # The $100 equity curves of the calm vaults, not the 10× outlier
    assert any(annotation.text == "▲ 3" for annotation in fig.layout.annotations)


def test_rolling_sharpe_chart():
    """The rolling Sharpe ratio matches the exported Sharpe ratio method, and the chart leaves out the T-bill."""
    index = pd.date_range("2026-01-01", periods=200, freq="D")
    rng = np.random.default_rng(42)
    prices = pd.DataFrame({"a": 100 * np.cumprod(1 + rng.normal(0.002, 0.01, len(index))), "b": 100 * np.cumprod(1 + rng.normal(0.001, 0.02, len(index)))}, index=index)
    sharpe = calculate_rolling_sharpe(prices["a"])
    expected = calculate_sharpe_ratio_from_returns(prices["a"].pct_change().iloc[-90:])
    assert sharpe.iloc[-1] == pytest.approx(expected)

    indices = {TREASURY_BILL: calculate_treasury_bill_index(pd.Series(0.04, index=index), index[-1]), BTC: prices["b"] * 2}
    series = [PerformanceSeries("a", "A", (), (BTC, TREASURY_BILL)), PerformanceSeries("b", "B", (), (BTC, TREASURY_BILL))]
    fig = create_performance_figure(series, prices, indices, DARK_THEME, measure="sharpe")
    assert [trace.name for trace in fig.data if trace.mode == "lines" and trace.name] == ["A", "B", BTC]
    assert fig.layout.yaxis.title.text == "Sharpe ratio, 90-day rolling"
    assert fig.layout.yaxis.range[0] == 0


def test_generate_report_bundle(tmp_path: Path, vaults_df: pd.DataFrame, prices_path: Path):
    """The bundle contains the post, tables and manifest, and reuses evergreen sections."""
    previous = GhostPost(
        id="p0",
        title="The best-performing stablecoin vaults, August 2026",
        slug="the-best-performing-stablecoin-vaults-august-2026",
        status="published",
        published_at=datetime.datetime(2026, 8, 25),
        updated_at=None,
        html='<h2 id="partners">Partners</h2><p>Thanks <a href="https://example.com?ref=trading-strategy.ghost.io">Example</a></p><h2 id="next-steps">Next steps</h2><p>Visit us</p>',
    )
    data = VaultReportData(vaults_df=vaults_df, prices_path=prices_path)
    report = generate_monthly_vault_report(
        data,
        output_dir=tmp_path / "out",
        previous=previous,
        changelog_entries=["Add Foo vault support (2026-09-01)"],
        render_charts=False,
    )

    assert report.slug == "the-best-performing-stablecoin-vaults-september-2026"
    post_html = (tmp_path / "out" / "post.html").read_text()
    assert "<!--kg-card-begin: html-->" in post_html
    assert "https://tradingstrategy.ai/blog/the-best-performing-stablecoin-vaults-august-2026" in post_html
    assert 'Thanks <a href="https://example.com">Example</a>' in post_html
    assert "Add Foo vault support" in post_html
    assert "Vault 0xcc" not in post_html  # Blacklisted
    manifest = json.loads((tmp_path / "out" / "report.json").read_text())
    assert manifest["sections"] == {"lending": 3, "perp_dex": 2, "perp_dex_sharpe": 2, "other": 1, "tokenised_funds": 1, "new": 1, "by_chain": 6}
    assert (tmp_path / "out" / "tables" / "lending.csv").exists()
    assert "5 of the 5 stablecoin yield vaults with at least $100k TVL beat the 3-month US Treasury bill yield of 4.0%" in post_html
    assert '<h3 id="best-performing-lending-vaults">' in post_html
    assert '<h2 id="the-best-performing-tokenised-funds">' in post_html
    # The per-chain table is a subsection of the best-performing vaults, before the tokenised funds
    assert '<h3 id="the-best-performing-vaults-on-each-chain">' in post_html
    assert post_html.index("the-best-performing-vaults-on-each-chain") < post_html.index("the-best-performing-tokenised-funds")
    assert "vault-sparklines.tradingstrategy.ai" in post_html

    # An existing draft is checked before any chart is uploaded
    report.chart_paths = {"lending_performance": tmp_path / "missing.png"}
    client = GhostAdminClient("https://example.ghost.io", "key:" + "00" * 32)
    client.session = FakeSession("draft")
    with pytest.raises(GhostAPIError):
        publish_report_draft(report, client)
    assert all(method == "GET" for method, _ in client.session.calls)


@pytest.mark.skipif(not CHOREOGRAPHER_CHROME_PATH.exists(), reason="Kaleido needs Chrome, install with plotly_get_chrome")
def test_render_report_charts(tmp_path: Path, vaults_df: pd.DataFrame, prices_path: Path):
    """All charts render as branded PNG panels, and the hero image has the social card size."""
    data = VaultReportData(vaults_df=vaults_df, prices_path=prices_path)
    report = generate_monthly_vault_report(data, output_dir=tmp_path / "out")
    assert set(report.chart_paths) == {
        "chain_yields",
        "protocol_yields",
        "protocol_high_yields",
        "protocol_tvl",
        "chain_tvl",
        "fund_nav",
        "tvl_changes",
        "lending_performance",
        "perp_dex_performance",
        "perp_dex_sharpe_performance",
        "other_performance",
        "tokenised_funds_performance",
        "risk_return",
        "by_chain_best",
    }
    for path in report.chart_paths.values():
        image = Image.open(path)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0  # Rounded panel corner is transparent
    assert Image.open(report.hero_path).size == HERO_SIZE
    assert Image.open(tmp_path / "out" / "hero-square.png").size == SQUARE_HERO_SIZE
    post_html = (tmp_path / "out" / "post.html").read_text()
    assert 'src="charts/lending_performance.png"' in post_html
    assert 'src="charts/tvl_changes.png"' in post_html


@pytest.mark.skipif(not CHOREOGRAPHER_CHROME_PATH.exists(), reason="Kaleido needs Chrome, install with plotly_get_chrome")
def test_render_report_charts_without_prices(tmp_path: Path, vaults_df: pd.DataFrame):
    """Missing price history leaves out the price charts instead of aborting the report."""
    empty_prices = tmp_path / "empty.parquet"
    pd.DataFrame({"id": ["1-0xother"], "timestamp": [pd.Timestamp(DATA_END_AT)], "share_price": [1.0], "total_assets": [1.0]}).to_parquet(empty_prices)
    data = VaultReportData(vaults_df=vaults_df, prices_path=empty_prices)
    report = generate_monthly_vault_report(data, output_dir=tmp_path / "out")
    assert set(report.chart_paths) == {"chain_yields", "protocol_yields", "protocol_high_yields", "tvl_changes", "risk_return", "by_chain_best"}
    assert "lending" in report.context.tables


def test_data_is_escaped_in_post(tmp_path: Path, vault_records: list[dict], prices_path: Path):
    """Vault names and denominations from the data cannot inject HTML into the post."""
    vault_records[0]["name"] = "<script>alert(1)</script>"
    vault_records[0]["normalised_denomination"] = "<b>USD</b>"
    data = VaultReportData(vaults_df=prepare_vault_metrics(vault_records), prices_path=prices_path)
    generate_monthly_vault_report(data, output_dir=tmp_path / "out", render_charts=False)
    post_html = (tmp_path / "out" / "post.html").read_text()
    assert "<script>" not in post_html
    assert "<b>USD</b>" not in post_html
    assert "&lt;b&gt;USD&lt;/b&gt;" in post_html


def test_choose_correlation_vaults_without_candidates(vaults_df: pd.DataFrame):
    """No vault with three-month returns gives an empty selection instead of an error."""
    df = vaults_df.assign(three_months_returns=float("nan"))
    assert len(choose_vaults_for_correlation_comparison(df, printer=lambda _: None)) == 0


def test_ghost_content_api_error_hides_key(monkeypatch: pytest.MonkeyPatch):
    """Connection errors do not expose the Content API key passed as a query parameter."""
    client = GhostContentClient("https://example.ghost.io", "secretkey123")

    def _fail(url: str, params: dict, timeout: float) -> None:
        raise requests.ConnectionError(f"Max retries exceeded with url: /ghost/api/content/posts/?key={params['key']}")

    monkeypatch.setattr(client.session, "get", _fail)
    with pytest.raises(GhostAPIError) as exc_info:
        client.fetch_latest_post_by_slug_prefix("prefix")
    assert "secretkey123" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__


def test_extract_section_html():
    """Sections are extracted up to the next h2 heading."""
    post_html = '<p>Intro</p><h2 id="partners">Partners</h2><p>A</p><h3>Sub</h3><h2 id="next-steps">Next</h2><p>B</p>'
    assert extract_section_html(post_html, "partners") == '<h2 id="partners">Partners</h2><p>A</p><h3>Sub</h3>'
    assert extract_section_html(post_html, "next-steps") == '<h2 id="next-steps">Next</h2><p>B</p>'
    assert extract_section_html(post_html, "missing") is None

    # Ghost tracking parameters are removed wherever they are in the query string
    links = '<h2 id="partners">P</h2><a href="https://a/x?ref=t.ghost.io">1</a><a href="https://a/x?page=0&ref=t.ghost.io">2</a><a href="https://a/x?ref=t.ghost.io&page=0">3</a>'
    assert extract_section_html(links, "partners") == '<h2 id="partners">P</h2><a href="https://a/x">1</a><a href="https://a/x?page=0">2</a><a href="https://a/x?page=0">3</a>'


def test_read_changelog_entries(tmp_path: Path):
    """Only new vault or protocol integrations after the date are offered."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# 1.2\n\n- feat: Add Foo vault discovery (2026-09-10).\n- feat: Recalculate vault metrics faster (2026-09-09)\n- fix: Add missing vault flag (2026-09-08)\n- feat: Add Bar protocol metadata (2026-08-01)\n")
    assert read_changelog_entries(changelog, since=datetime.date(2026, 8, 25)) == ["Add Foo vault discovery (2026-09-10)"]
    assert make_report_slug(datetime.datetime(2026, 2, 25)) == "the-best-performing-stablecoin-vaults-february-2026"


def test_create_ghost_admin_token():
    """Admin API tokens are HS256 JWTs signed with the hex-decoded secret."""
    secret = "a1" * 32
    token = create_ghost_admin_token(f"key123:{secret}", now=datetime.datetime(2026, 9, 25))
    header_b64, payload_b64, signature_b64 = token.split(".")

    def _decode(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    assert json.loads(_decode(header_b64)) == {"alg": "HS256", "typ": "JWT", "kid": "key123"}
    payload = json.loads(_decode(payload_b64))
    assert payload["aud"] == "/admin/"
    assert payload["exp"] - payload["iat"] == 300
    expected_signature = hmac.new(bytes.fromhex(secret), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
    assert _decode(signature_b64) == expected_signature

    with pytest.raises(AssertionError):
        create_ghost_admin_token("content-api-key-without-secret")


class FakeResponse:
    """Minimal requests response stand-in."""

    def __init__(self, status_code: int, data: dict) -> None:
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self) -> dict:
        return self._data


class FakeSession:
    """Records requests and serves a fixed existing post."""

    def __init__(self, existing_status: str | None) -> None:
        self.headers = {}
        self.existing_status = existing_status
        self.calls = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("GET", url))
        if self.existing_status is None:
            return FakeResponse(404, {"errors": [{"message": "Not found"}]})
        return FakeResponse(200, {"posts": [{"id": "p1", "slug": "s", "status": self.existing_status, "updated_at": "2026-09-01T00:00:00.000Z"}]})

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("POST", url))
        return FakeResponse(201, {"posts": [{"id": "p2", "slug": "s", "status": "draft"}]})

    def put(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("PUT", url))
        assert kwargs["json"]["posts"][0]["updated_at"] == "2026-09-01T00:00:00.000Z"
        return FakeResponse(200, {"posts": [{"id": "p1", "slug": "s", "status": "draft"}]})


@pytest.mark.parametrize(
    ("existing_status", "overwrite", "expected"),
    [
        (None, False, "POST"),
        ("draft", True, "PUT"),
        ("draft", False, GhostAPIError),
        ("published", True, GhostAPIError),
    ],
)
def test_create_or_update_draft(existing_status: str | None, overwrite: bool, expected: str | type):
    """Drafts are created, overwritten only on request, and published posts are never touched."""
    client = GhostAdminClient("https://example.ghost.io", "key:" + "00" * 32)
    client.session = FakeSession(existing_status)
    if expected is GhostAPIError:
        with pytest.raises(GhostAPIError):
            client.create_or_update_draft("Title", "s", "<p>Body</p>", overwrite_draft=overwrite)
        assert all(method == "GET" for method, _ in client.session.calls)
    else:
        post = client.create_or_update_draft("Title", "s", "<p>Body</p>", overwrite_draft=overwrite)
        assert post.status == "draft"
        assert client.session.calls[-1][0] == expected


def test_treasury_bill_benchmark():
    """Daily accrual, with weekends forward filled, matches compounding."""
    yields = pd.Series(0.0365, index=pd.date_range("2026-01-02", periods=200, freq="B"))
    index = calculate_treasury_bill_index(yields, datetime.datetime(2026, 6, 30))
    assert index.loc["2026-06-30"] / index.loc["2026-04-01"] == pytest.approx((1 + 0.0365 / 365) ** 90)


def test_select_benchmarks(vaults_df: pd.DataFrame):
    """Benchmarks follow the website rules for perp and GMX vaults, and vault activity otherwise."""

    def _select(**overrides) -> tuple[str, ...]:
        vault = vaults_df.loc["1-0xaa"].copy()
        for key, value in overrides.items():
            vault[key] = value
        return select_benchmarks(vault, min_volatility=0.25, max_drawdown=-0.10)

    assert _select() == (TREASURY_BILL,)
    assert _select(flags=["perp_dex_trading_vault"]) == (BTC, ETH)
    assert _select(chain_id=325) == (BTC, ETH)
    assert _select(three_months_volatility=0.4) == (BTC, ETH)
    assert _select(three_months_max_drawdown=-0.2) == (BTC, ETH)
    assert _select(protocol_slug="gmx", vault_slug="gm-btc-usdc") == (BTC,)
    assert _select(protocol_slug="gmx", vault_slug="gm-swap-usdc-usdt", name="GM swap [USDC-USDT]") == (TREASURY_BILL,)
    assert _select(protocol_slug="gmx", vault_slug="glv-weth-usdc") == (BTC, ETH)


def test_protocol_tvl_history(vaults_df: pd.DataFrame, prices_path: Path):
    """Weekly TVL is summed per protocol with the tail grouped as Other."""
    tvl = read_vault_tvl_history(prices_path, list(vaults_df.index), start_at=DATA_END_AT - datetime.timedelta(days=60), end_at=DATA_END_AT)
    # Like the website: weekly averages of daily closing values, without the report date's incomplete week
    assert tvl.index.max() < pd.Timestamp(DATA_END_AT).to_period("W").start_time
    assert pd.Timedelta(tvl.index.to_series().diff().dropna().unique()[0]) == pd.Timedelta(days=7)
    by_protocol = calculate_protocol_tvl_history(tvl, vaults_df, top_n=1)
    assert list(by_protocol.columns) == ["Morpho", "Other"]
    assert by_protocol.iloc[-1].sum() == pytest.approx(tvl.iloc[-1].sum())

    # Generic ERC-4626 vaults are never a protocol of their own, but part of the Other pile
    by_protocol = calculate_protocol_tvl_history(tvl, vaults_df, top_n=10)
    assert "ERC-4626" not in by_protocol.columns
    assert by_protocol["Other"].iloc[-1] == pytest.approx(tvl["1-0x33"].iloc[-1])

    # Tokenised fund NAV is grouped per fund name, without Other when every fund is shown
    funds = vaults_df.loc[vaults_df["group"] == TOKENISED_FUND]
    by_fund = calculate_fund_nav_history(tvl[list(funds.index)], funds)
    assert list(by_fund.columns) == list(funds["name"])
    assert by_fund.iloc[-1].sum() == pytest.approx(tvl[list(funds.index)].iloc[-1].sum())

    # Blockchain TVL keeps the largest chains and sums the rest as Other
    by_chain = calculate_chain_tvl_history(tvl, vaults_df, top_n=1)
    largest_chain = tvl.iloc[-1].groupby(vaults_df["chain"]).sum().idxmax()
    assert list(by_chain.columns) == [largest_chain, "Other"]
    assert by_chain.iloc[-1].sum() == pytest.approx(tvl.iloc[-1].sum())


def test_unidentified_protocols(vaults_df: pd.DataFrame):
    """Generic ERC-4626, unknown and placeholder protocols form one Other pile, like on the website."""
    assert is_identified_protocol("Morpho", "morpho")
    assert not is_identified_protocol("ERC-4626", "erc-4626")
    assert not is_identified_protocol("<unknown ERC-4626>", "protocol-not-yet-identified")
    assert not is_identified_protocol("Unknown vault protocol", "unknown")
    assert not is_identified_protocol("Some vault", "unknown-erc-7450")
    assert not is_identified_protocol(None, None)
    assert vaults_df.loc["1-0x33", "protocol_label"] == OTHER_PROTOCOL
    assert format_vault_cells(vaults_df.loc["1-0x33"])["Protocol"] == OTHER_PROTOCOL


def test_chart_risk_filter(vaults_df: pd.DataFrame):
    """Charts leave out vaults rated Dangerous or worse; unrated and Severe vaults stay."""
    df = vaults_df.loc[["1-0xaa", "1-0xbb", "1-0x22", "1-0x55"]].copy()
    df["risk_numeric"] = [20.0, 40.0, 50.0, float("nan")]
    assert list(exclude_chart_risks(df, ReportCriteria())["address"]) == ["0xaa", "0xbb", "0x55"]


def test_risk_return_scales(vaults_df: pd.DataFrame):
    """Risk and return fits the bulk of vaults, marks outliers on the edges and leaves dormant vaults out."""
    df = vaults_df.loc[["1-0xaa", "1-0xbb", "1-0x22", "1-0x55", "1-0x44"]].copy()
    df["three_months_volatility"] = [0.001, 0.002, 0.003, 0.0, 0.004]
    df["three_months_cagr_best"] = [0.04, 0.05, 0.06, 0.0, 0.05]
    # 0x55 has a flat share price and is dormant
    assert "1-0x55" not in select_moving_vaults(df).index

    # Many ordinary vaults and one 500% outlier: the axis fits the ordinary ones and the outlier is an edge triangle
    many = pd.concat([df.iloc[[0]].assign(three_months_cagr_best=0.03 + i / 1000) for i in range(100)], ignore_index=True)
    many.index = [f"v{i}" for i in range(len(many))]
    many.loc["v99", "three_months_cagr_best"] = 5.0
    fig = create_risk_return_figure(many, {}, DARK_THEME, max_return=1.0, benchmark_yield=0.042)
    assert fig.layout.yaxis.range[1] < 50  # Far below the 500% outlier
    off_scale = [trace for trace in fig.data if trace.name and trace.name.startswith("Off scale")]
    assert len(off_scale) == 1 and list(off_scale[0].marker.symbol) == ["triangle-up"]


def test_vault_properties(vaults_df: pd.DataFrame):
    """Charts show the curator, protocol and chain under a vault name, without repeating the protocol."""
    vault = vaults_df.loc["1-0xaa"].copy()
    vault["curator_name"], vault["curator_slug"] = "Steakhouse Financial", "steakhouse"
    properties = make_vault_properties(vault, DARK_THEME, lambda chain: f"logo:{chain}")
    assert [prop.text for prop in properties] == ["Steakhouse Financial", "Morpho", "Ethereum"]
    assert properties[2].logo_uri == "logo:Ethereum"

    # A protocol curating its own vault, on a chain named after the protocol, is shown once
    vault["curator_name"], vault["curator_slug"], vault["chain"] = "Morpho", "morpho", "Morpho"
    assert [prop.text for prop in make_vault_properties(vault, DARK_THEME, lambda chain: None)] == ["Morpho"]


def test_table_sparklines(vaults_df: pd.DataFrame):
    """Tables show sparklines only for vaults that have one, and no risk rating column."""
    table = render_section_table(ReportSection(vaults_df.loc[["1-0xaa", "1-0xbb"]], sparkline_ids=frozenset({"1-0xaa"})))
    assert table.count("sparkline-90d-") == 1
    assert "sparkline-90d-1-0xaa.png" in table
    assert table.startswith("<table>")
    assert "Risk" not in table


def test_compose_chart_panel(tmp_path: Path):
    """The panel crops the chart to its content and pads it evenly, with transparent rounded corners."""
    chart = tmp_path / "chart.png"
    render = Image.new("RGBA", (1400, 800), (0, 0, 0, 0))
    ImageDraw.Draw(render).rectangle((100, 100, 1299, 699), fill=DARK_THEME.series_colours[0])
    render.save(chart)
    output = compose_chart_panel(chart, DARK_THEME, "Title", "Subtitle", "Data 2026-09-25", "tradingstrategy.ai", tmp_path / "panel.png", scale=1)
    image = Image.open(output)
    # The 1200×600 content is resized to the 1312 px inner width: header 120, gaps 2×28, footer 107
    assert image.size == (PANEL_WIDTH, 120 + 28 + 656 + 28 + 107)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((PANEL_PADDING - 1, 500))[:3] != image.getpixel((PANEL_PADDING + 1, 500))[:3]
    assert image.getpixel((PANEL_WIDTH - PANEL_PADDING, 500))[:3] != image.getpixel((PANEL_WIDTH - PANEL_PADDING - 2, 500))[:3]

    # Long titles and subtitles wrap to more lines instead of being truncated, and the header grows
    long_title = "Performance of the best-performing perpetual futures DEX vaults with the best Sharpe ratio this month"
    output = compose_chart_panel(chart, DARK_THEME, long_title, f"{long_title}, {long_title}", "Data 2026-09-25", "tradingstrategy.ai", tmp_path / "long.png", scale=1)
    assert Image.open(output).size == (PANEL_WIDTH, 120 + 50 + 32 + 28 + 656 + 28 + 107)  # One more title line and one more subtitle line

    # At the export scale every measure grows by 4/3, also when the chart content needs resizing
    output = compose_chart_panel(chart, DARK_THEME, "Title", "Subtitle", "Data 2026-09-25", "tradingstrategy.ai", tmp_path / "scaled.png")
    image = Image.open(output)
    assert image.width == round(PANEL_WIDTH * CHART_SCALE) == 1867
    assert image.height == round(120 * CHART_SCALE) + 2 * round(28 * CHART_SCALE) + round(600 * (1867 - 2 * 59) / 1200) + round(107 * CHART_SCALE)
    title_rows = [y for y in range(8, 110) if any(min(image.getpixel((x, y))) > 200 for x in range(59, 200))]  # Opaque white title text, below the panel border
    assert title_rows[0] == pytest.approx(59, abs=2)  # The top margin matches the 59 px side margin


def test_trim_logos():
    """Logos lose their transparent margins, so each icon box fits its mark."""
    logo = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((38, 8, 57, 87), fill=(255, 0, 0, 255))  # A narrow 20×80 mark
    buffer = io.BytesIO()
    logo.save(buffer, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    trimmed_uri, aspect = trim_logos({uri})[uri]
    trimmed = Image.open(io.BytesIO(base64.b64decode(trimmed_uri.split(",", 1)[1])))
    assert trimmed.size == (20, 80)
    assert aspect == 0.25

    # The icon keeps the row height and its own width; very wide logos are scaled down
    assert VaultProperty("Ethereum", trimmed_uri, aspect).icon_size == (17 * 0.25, 17)
    assert VaultProperty("Wide", trimmed_uri, 4.0).icon_size == (34, 8.5)


def test_wrap_label():
    """Chart labels wrap to full length instead of being truncated."""
    label = wrap_label("Janus Henderson Anemoy Treasury Fund", 20)
    assert label == "Janus Henderson<br>Anemoy Treasury Fund"
    assert wrap_label("Morpho", 16) == "Morpho"


# ---------------------------------------------------------------------------
# Investability check
# ---------------------------------------------------------------------------


def _decisions_document(candidates: list[CheckCandidate], digest: str, decisions: list[dict], data_end_at: datetime.datetime = DATA_END_AT) -> dict:
    """A decisions file document as the agent writes it."""
    return {"schema_version": SCHEMA_VERSION, "scope_version": SCOPE_VERSION, "rules_version": RULES_VERSION, "data_end_at": data_end_at.isoformat(), "candidates_digest": digest, "decisions": decisions}


def _exclusion(vault_id: str, **overrides) -> dict:
    """A valid exclusion record."""
    record = {"vault_id": vault_id, "decision": "exclude", "category": "suspicious_collateral", "suspicious_item": "RSS collateral", "reason": "Lends against a token with no market.", "evidence": [{"source": "https://example.com", "observed_at": DATA_END_AT.isoformat()}], "confidence": "high"}
    record.update(overrides)
    return record


def test_check_candidates_follow_report_selectors(vaults_df: pd.DataFrame):
    """Candidates come from each real selector and metric, deduplicated, with their lists and ranks."""
    criteria = ReportCriteria()
    comparable = select_comparable_vaults(filter_eligible_vaults(vaults_df, DATA_END_AT, criteria))
    lists = collect_top_lists(comparable, criteria)
    assert {"table:lending", "chart:lending", "chart:hero", "table:by_chain", "chart:by_chain_best", "table:new"} <= set(lists)
    candidates = build_check_candidates(lists)
    # 0xaa ranks first by one-month return in the lending table and first by three-month return in the lending chart
    assert "table:lending#1" in candidates["1-0xaa"].lists
    assert any(item.startswith("chart:lending#") for item in candidates["1-0xaa"].lists)
    assert candidates["1-0xaa"].in_scope
    assert not candidates["1-0xff"].in_scope  # Hyperliquid is not in version 1 scope
    assert candidate_depth(20, 0.5) == 30


def test_check_decisions_validation(tmp_path: Path, vaults_df: pd.DataFrame):
    """Decisions must match the round, cover every in-scope candidate and carry evidence."""
    candidates = list(build_check_candidates({"table:lending": vaults_df.loc[["1-0xaa", "1-0xbb"]]}).values())
    digest = write_candidates_file(tmp_path / "candidates.json", candidates, DATA_END_AT)
    path = tmp_path / "decisions.json"

    def check(decisions: list[dict], **header) -> dict:
        document = _decisions_document(candidates, digest, decisions) | header
        path.write_text(json.dumps(document))
        return read_check_decisions(path, candidates, digest, DATA_END_AT)

    valid = [_exclusion("1-0xaa"), {"vault_id": "1-0xbb", "decision": "keep"}]
    assert check(valid)["1-0xaa"].decision == "exclude"
    failures = {
        "other month": lambda: check(valid, data_end_at="2026-08-24T00:00:00"),
        "changed candidates": lambda: check(valid, candidates_digest="0" * 64),
        "missing decision": lambda: check(valid[:1]),
        "unknown vault": lambda: check([*valid, {"vault_id": "1-0x99", "decision": "keep"}]),
        "duplicate": lambda: check([*valid, {"vault_id": "1-0xbb", "decision": "keep"}]),
        "not checked": lambda: check([valid[0], {"vault_id": "1-0xbb", "decision": "not_in_scope"}]),
        "no evidence": lambda: check([_exclusion("1-0xaa", evidence=[]), valid[1]]),
        "blacklist without high confidence": lambda: check([_exclusion("1-0xaa", blacklist=True, vault_flag="misleading_valuation", confidence="medium"), valid[1]]),
        "blacklist with a harmless flag": lambda: check([_exclusion("1-0xaa", blacklist=True, vault_flag="trading"), valid[1]]),
        "stale liquidity evidence": lambda: check([_exclusion("1-0xaa", category="no_exit_liquidity", evidence=[{"source": "x", "observed_at": "2026-08-01T00:00:00"}]), valid[1]]),
    }
    for name, failure in failures.items():
        with pytest.raises(CheckValidationError):
            failure()
            pytest.fail(name)
    path.unlink()
    with pytest.raises(CheckValidationError):
        read_check_decisions(path, candidates, digest, DATA_END_AT)


def test_blacklist_entry_check(tmp_path: Path):
    """A blacklisted vault must have a flag.py entry with its flag."""
    decisions = {"8453-0xf80c": CheckDecision(vault_id="8453-0xf80c", decision="exclude", blacklist=True, vault_flag="misleading_valuation")}
    flag_file = tmp_path / "flag.py"
    flag_file.write_text('VAULT_FLAGS_AND_NOTES = {\n    "0xabc": (VaultFlag.illiquid, X),\n}\n')
    assert check_blacklist_entries(decisions, flag_file) == ["8453-0xf80c"]
    flag_file.write_text('VAULT_FLAGS_AND_NOTES = {\n    # King RSS\n    "0xf80c": (VaultFlag.misleading_valuation, KING_RSS),\n}\n')
    assert check_blacklist_entries(decisions, flag_file) == []
    # Formatting does not matter: the entry may be wrapped over several lines
    flag_file.write_text('VAULT_FLAGS_AND_NOTES: dict = {\n    "0xF80C": (\n        VaultFlag.misleading_valuation,\n        KING_RSS,\n    ),\n}\n')
    assert check_blacklist_entries(decisions, flag_file) == []
    decisions["8453-0xf80c"] = CheckDecision(vault_id="8453-0xf80c", decision="exclude", blacklist=True, vault_flag="illiquid")
    assert check_blacklist_entries(decisions, flag_file) == ["8453-0xf80c"]  # Another flag


def test_check_agent_timeout_stops_child_processes(tmp_path: Path):
    """A timed-out agent is stopped with the tools it started, so nothing keeps writing files."""
    marker = tmp_path / "late.txt"
    # The agent starts a tool that would write a file after the timeout, then waits for it
    tool = tmp_path / "tool.py"
    tool.write_text(f"import time\ntime.sleep(3)\nopen({str(marker)!r}, 'w').write('x')\n")
    agent_script = tmp_path / "agent.py"
    agent_script.write_text(f"import subprocess, sys\nsubprocess.run([sys.executable, {str(tool)!r}])\n")
    agent = [sys.executable, str(agent_script)]
    with pytest.raises(CheckValidationError, match="timed out"):
        run_check_agent(agent, tmp_path, tmp_path / "agent.jsonl", tmp_path / "decisions.json", timeout=0.5, poll_interval=0.2)
    time.sleep(4)
    assert not marker.exists()


def test_vault_links_are_web_links(vaults_df: pd.DataFrame):
    """A link that is not an https:// URL is shown as plain text, never as a clickable link."""
    row = vaults_df.iloc[0].copy()
    row["trading_strategy_link"] = "javascript:alert(1)"
    assert "<a " not in format_vault_cells(row)["Vault"]
    row["trading_strategy_link"] = "https://tradingstrategy.ai/vaults/foo"
    assert format_vault_cells(row)["Vault"].startswith('<a href="https://tradingstrategy.ai/vaults/foo">')


def test_check_agent_runner(tmp_path: Path):
    """The runner accepts only a freshly written decisions file from a clean exit."""
    decisions = tmp_path / "decisions.json"
    writer = [sys.executable, "-c", f"open({str(decisions)!r}, 'w').write('{{}}')"]
    decisions.write_text("stale")
    run_check_agent(writer, tmp_path, tmp_path / "agent.jsonl", decisions, timeout=60)
    assert decisions.read_text() == "{}"

    decisions.write_text("stale")
    with pytest.raises(CheckValidationError):
        run_check_agent([sys.executable, "-c", "pass"], tmp_path, tmp_path / "agent.jsonl", decisions, timeout=60)
    assert not decisions.exists()  # A stale file is never accepted
    with pytest.raises(CheckValidationError):
        run_check_agent([sys.executable, "-c", "raise SystemExit(3)"], tmp_path, tmp_path / "agent.jsonl", decisions, timeout=60)

    # Both CLIs run unsandboxed with web search
    assert build_agent_command("codex", "p", "gpt-6-sol")[:3] == ["codex", "--search", "exec"]
    assert "danger-full-access" in build_agent_command("codex", "p")
    assert "WebSearch" in build_agent_command("claude", "p")[build_agent_command("claude", "p").index("--allowedTools") + 1]


def test_excluded_vault_leaves_all_rankings(tmp_path: Path, vaults_df: pd.DataFrame, prices_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An excluded vault leaves every table and the caption, and is listed in the excluded section."""
    monkeypatch.setattr(vault_checks_module, "fetch_candidate_facts", lambda candidates, prices_path, end_at, max_workers: {})

    def fake_agent(command, cwd, log_path, decisions_path, timeout):
        round_number = decisions_path.stem.rsplit("-", 1)[1]
        document = json.loads((decisions_path.parent / f"vault-check-candidates-{round_number}.json").read_text())
        digest = hashlib.sha256((decisions_path.parent / f"vault-check-candidates-{round_number}.json").read_bytes()).hexdigest()
        records = [_exclusion(c["vault_id"], reason="<script>alert(1)</script> no market") if c["vault_id"] == "1-0xaa" else {"vault_id": c["vault_id"], "decision": "keep"} for c in document["candidates"]]
        decisions_path.write_text(json.dumps({**{k: document[k] for k in ("schema_version", "scope_version", "rules_version", "data_end_at")}, "candidates_digest": digest, "decisions": records}))

    monkeypatch.setattr(vault_checks_module, "run_check_agent", fake_agent)
    monkeypatch.setattr(vault_checks_module, "show_flag_diff", lambda root: "")
    data = VaultReportData(vaults_df=vaults_df, prices_path=prices_path)
    settings = VaultCheckSettings(agent="claude", repository_root=tmp_path)
    (tmp_path / "eth_defi" / "vault").mkdir(parents=True)
    (tmp_path / "eth_defi" / "vault" / "flag.py").write_text("")
    report = generate_monthly_vault_report(data, output_dir=tmp_path / "out", render_charts=False, check_sparklines=False, vault_checks=settings)

    assert report.vault_checks.excluded == frozenset({"1-0xaa"})
    assert all("1-0xaa" not in section.vaults_df.index for section in report.sections.values())
    post_html = (tmp_path / "out" / "post.html").read_text()
    assert '<h2 id="excluded-vaults-in-this-report">' in post_html
    excluded_section = post_html[post_html.index("excluded-vaults-in-this-report") :]
    assert "Vault 0xaa" in excluded_section and "Vault 0xaa" not in post_html[: post_html.index("excluded-vaults-in-this-report")]
    assert "<script>" not in post_html and "&lt;script&gt;" in excluded_section
    manifest = json.loads((tmp_path / "out" / "report.json").read_text())
    assert manifest["vault_checks"]["excluded"][0]["vault_id"] == "1-0xaa"
    assert (tmp_path / "out" / "tables" / "excluded.csv").exists()

    # A rerun on the same data reuses the saved decisions instead of running the agent again
    monkeypatch.setattr(vault_checks_module, "run_check_agent", lambda *args: pytest.fail("agent must not run"))
    rerun = generate_monthly_vault_report(data, output_dir=tmp_path / "out", render_charts=False, check_sparklines=False, vault_checks=settings)
    assert rerun.vault_checks.excluded == frozenset({"1-0xaa"})


def test_report_without_check_has_editor_note(tmp_path: Path, vaults_df: pd.DataFrame, prices_path: Path):
    """Without the check, the post tells the editor so."""
    data = VaultReportData(vaults_df=vaults_df, prices_path=prices_path)
    generate_monthly_vault_report(data, output_dir=tmp_path / "out", render_charts=False, check_sparklines=False)
    post_html = (tmp_path / "out" / "post.html").read_text()
    assert "without the investability check" in post_html
    assert "excluded-vaults-in-this-report" not in post_html


def test_probe_signals():
    """Signals use redeemable, not idle, liquidity and flag collateral without a market."""
    liquid = VaultFacts(vault_id="1-0x1", probe="morpho_v1", total_assets=100.0, idle_assets=0.0, redeemable_assets=40.0, redeemable_share=0.4, exposures=[Exposure(market="m", kind="morpho_market", assets=90.0, share_of_assets=0.9, collateral="0xc", collateral_symbol="cbBTC", collateral_dex_liquidity_usd=50e6)])
    assert raise_signals(liquid) == []  # No idle cash, but plenty of queue liquidity
    stuck = VaultFacts(vault_id="1-0x2", probe="morpho_v1", total_assets=100.0, idle_assets=0.0, redeemable_assets=0.0, redeemable_share=0.0, exposures=[Exposure(market="m", kind="morpho_market", assets=95.0, share_of_assets=0.95, collateral="0xd", collateral_symbol="RSS", collateral_dex_liquidity_usd=0.0)])
    signals = raise_signals(stuck)
    assert any("RSS" in signal for signal in signals) and any("redeemable" in signal for signal in signals)
    assert select_probe("morpho", ["morpho_like", "euler_earn_like"]) == "morpho_v1"
    assert select_probe("euler", ["euler_earn_like"]) == "euler_earn"
    assert select_probe("40acres", []) == "forty_acres"
    assert select_probe("yearn", []) == "unsupported"


# ---------------------------------------------------------------------------
# Latest podcasts
# ---------------------------------------------------------------------------

EPISODE_HTML = """<p>Listen to our latest episode featuring <a href="https://tradingstrategy.ai/vaults/curators/yearn?ref=trading-strategy.ghost.io">Yearn</a> &amp; learn about <b>vaults</b>.</p>
<figure class="kg-card kg-embed-card"><iframe src="https://www.youtube.com/embed/TM9Wn73Qxt8?feature=oembed"></iframe></figure>
<p><a href="https://www.youtube.com/watch?v=TM9Wn73Qxt8&amp;si=abc&amp;ref=trading-strategy.ghost.io">Watch the Yearn episode on Youtube</a></p>
<p><a href="https://open.spotify.com/episode/36SGS7zXb0buGqORsmYqIw?si=ghMV&amp;ref=trading-strategy.ghost.io">Listen on Spotify</a></p>"""


def make_episode_post(slug: str, title: str, body: str = EPISODE_HTML) -> GhostPost:
    """A published podcast episode post."""
    return GhostPost(id=slug, title=title, slug=slug, status="published", html=body, published_at=datetime.datetime(2026, 9, 21), updated_at=None)


def test_parse_podcast_episode():
    """The promotion text, clean Spotify and YouTube links and the guest's logo slug are read from the post."""
    episode = parse_podcast_episode(make_episode_post("episode-13-yearn", "Episode #13: Yearn"))
    assert episode.promotion == "Listen to our latest episode featuring Yearn & learn about vaults."
    assert episode.url == "https://tradingstrategy.ai/blog/episode-13-yearn"
    assert episode.youtube_url == "https://www.youtube.com/watch?v=TM9Wn73Qxt8"  # A watch link, not the embed, without tracking
    assert episode.spotify_url == "https://open.spotify.com/episode/36SGS7zXb0buGqORsmYqIw"
    assert episode.logo_slug == "yearn"
    assert episode.guest == "Yearn"

    bare = parse_podcast_episode(make_episode_post("episode-1-x", "Episode #1: X", "<p>Promo</p>"))
    assert (bare.spotify_url, bare.youtube_url, bare.logo_slug) == (None, None, None)
    rendered = render_podcast_episodes([bare], {})
    assert "Spotify" not in rendered and "YouTube" not in rendered and "<img" not in rendered  # Only the title links, to the blog post


def test_latest_podcasts_section(tmp_path: Path, vaults_df: pd.DataFrame, prices_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The podcasts come before the data analytics sections, with their logos bundled and uploaded on publish."""
    episodes = [
        parse_podcast_episode(make_episode_post("episode-13-yearn", "Episode #13: Yearn")),
        parse_podcast_episode(make_episode_post("episode-99-evil", "Episode #99: <script>", "<p>Promo <script>alert(1)</script></p>")),
    ]
    data = VaultReportData(vaults_df=vaults_df, prices_path=prices_path)
    report = generate_monthly_vault_report(data, output_dir=tmp_path / "out", render_charts=False, check_sparklines=False, podcasts=episodes)

    post_html = (tmp_path / "out" / "post.html").read_text()
    # Offline reports have no charts, so the first data section is the best-performing vaults
    assert post_html.index("defi-vault-community-news") < post_html.index('<h2 id="latest-podcasts">Latest podcasts</h2>') < post_html.index('id="the-best-performing-vaults"')
    assert '<img src="podcasts/yearn.png" alt="Yearn logo"' in post_html
    assert '<a href="https://open.spotify.com/episode/36SGS7zXb0buGqORsmYqIw"><img src="podcasts/icons/spotify.png" alt=""' in post_html
    assert '<img src="podcasts/icons/youtube.png" alt=""' in post_html
    assert (tmp_path / "out" / "podcasts" / "icons" / "youtube.png").exists()
    assert "<script>" not in post_html
    assert (tmp_path / "out" / "podcasts" / "yearn.png").exists()
    manifest = json.loads((tmp_path / "out" / "report.json").read_text())
    assert [podcast["title"] for podcast in manifest["podcasts"]] == ["Episode #13: Yearn", "Episode #99: <script>"]

    # Publishing uploads the logos with the charts and links them in the draft
    client = GhostAdminClient("https://example.ghost.io", "key:" + "00" * 32)
    drafts = []
    monkeypatch.setattr(client, "fetch_writable_draft", lambda slug, overwrite_draft: None)
    monkeypatch.setattr(client, "upload_image", lambda path: f"https://ghost.example/{path.name}")
    monkeypatch.setattr(client, "create_or_update_draft", lambda **kwargs: drafts.append(kwargs) or GhostPost("d1", kwargs["title"], kwargs["slug"], "draft", None, None, None))
    monkeypatch.setattr(client, "get_editor_url", lambda post: "https://example.ghost.io/ghost/#/editor/post/d1")
    publish_report_draft(report, client)
    assert '<img src="https://ghost.example/yearn.png"' in drafts[0]["html"]
    assert '<img src="https://ghost.example/spotify.png"' in drafts[0]["html"]

    # Without episodes the section is left out
    generate_monthly_vault_report(data, output_dir=tmp_path / "empty", render_charts=False, check_sparklines=False)
    assert "latest-podcasts" not in (tmp_path / "empty" / "post.html").read_text()
    assert not (tmp_path / "empty" / "podcasts").exists()


def test_rwa_vaults_section(tmp_path: Path, vault_records: list[dict], prices_path: Path):
    """RWA-tagged vaults get their own group and subsection, including RWA lending vaults."""
    rwa = make_vault_record("0x66", protocol="Lagoon Finance", protocol_slug="lagoon-finance", strategy_tags=["rwa_lending"], one_month_cagr_net=0.14)
    assert classify_vault(prepare_vault_metrics([rwa]).iloc[0]) == RWA
    fund = make_vault_record("0x77", strategy_tags=["rwa"], flags=["tokenised_fund"])
    assert classify_vault(prepare_vault_metrics([fund]).iloc[0]) == TOKENISED_FUND  # Tokenised funds keep their own section

    data = VaultReportData(vaults_df=prepare_vault_metrics([*vault_records, rwa]), prices_path=prices_path)
    report = generate_monthly_vault_report(data, output_dir=tmp_path / "out", render_charts=False, check_sparklines=False)
    assert list(report.sections["rwa"].vaults_df.index) == ["1-0x66"]
    assert "1-0x66" not in report.sections["lending"].vaults_df.index
    post_html = (tmp_path / "out" / "post.html").read_text()
    assert post_html.index('<h3 id="best-performing-lending-vaults">') < post_html.index('<h3 id="best-performing-rwa-vaults">Real-world asset (RWA) vaults</h3>') < post_html.index('<h3 id="best-performing-perp-dex-vaults">')


def test_legacy_vault_links_rewritten():
    """Exports made before the website moved vault pages link to /vaults/ in the report."""
    records = [
        make_vault_record("0x81", trading_strategy_link="https://tradingstrategy.ai/trading-view/vaults/foo"),
        make_vault_record("0x82", trading_strategy_link="https://tradingstrategy.ai/trading-view/base/vaults/bar"),
        make_vault_record("0x83"),
    ]
    links = prepare_vault_metrics(records)["trading_strategy_link"].tolist()
    assert links == ["https://tradingstrategy.ai/vaults/foo", "https://tradingstrategy.ai/vaults/bar", "https://tradingstrategy.ai/vaults/0x83"]


def test_chart_text_is_escaped():
    """Vault names are shown as written in charts, never read as markup."""
    assert plain_text("A&B <script>") == "A&amp;B &lt;script&gt;"
    assert wrap_label("<b>Evil</b> vault", 40) == "&lt;b&gt;Evil&lt;/b&gt; vault"
    fig = go.Figure()
    fig.update_layout(width=1000, height=600, margin={"l": 50, "r": LEGEND_MARGIN, "t": 20, "b": 20})
    add_logo_legend(fig, [LegendEntry("1. <img src=x>", "#ff0000", properties=(VaultProperty("<i>Curator</i>"),))], DARK_THEME)
    texts = [annotation.text for annotation in fig.layout.annotations]
    assert "<b>1. &lt;img src=x&gt;</b>" in texts
    assert "&lt;i&gt;Curator&lt;/i&gt;" in texts


def test_probe_contains_rpc_failures(monkeypatch: pytest.MonkeyPatch):
    """A dead RPC is recorded as a probe error, so one chain cannot stop the check."""

    def dead_rpc(url: str):
        raise RuntimeError("Could not connect to any provider")

    monkeypatch.setattr(vault_probes_module, "read_json_rpc_url", lambda chain_id: "https://dead.example")
    monkeypatch.setattr(vault_probes_module, "create_multi_provider_web3", dead_rpc)
    facts = vault_probes_module.fetch_vault_facts("1-0x1234", "morpho", [], {})
    assert facts.errors and "Could not connect" in facts.errors[0]

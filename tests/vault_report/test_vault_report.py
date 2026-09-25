"""Unit tests for the monthly vault report pipeline, using synthetic data."""

import base64
import datetime
import hashlib
import hmac
import json
from pathlib import Path

import pandas as pd
import pytest
import requests
from PIL import Image

from eth_defi.research.vault_correlation import choose_vaults_for_correlation_comparison
from eth_defi.vault_report import report as report_module
from eth_defi.vault_report.benchmarks import calculate_treasury_bill_rolling_returns
from eth_defi.vault_report.branding import HERO_SIZE, SQUARE_HERO_SIZE, compose_chart_panel
from eth_defi.vault_report.charts import CHOREOGRAPHER_CHROME_PATH, calculate_rolling_returns, create_correlation_figure, create_rolling_returns_figure
from eth_defi.vault_report.data import VaultReportData, calculate_daily_share_prices, prepare_vault_metrics, read_vault_share_prices, read_vault_tvl_history
from eth_defi.vault_report.ghost import GhostAdminClient, GhostAPIError, GhostContentClient, GhostPost, create_ghost_admin_token
from eth_defi.vault_report.movers import calculate_rank_changes, parse_ranked_vault_links, resolve_vault_id
from eth_defi.vault_report.post import extract_section_html, make_report_slug, read_changelog_entries
from eth_defi.vault_report.report import generate_monthly_vault_report, publish_report_draft, read_previous_ranking
from eth_defi.vault_report.sections import (
    ReportCriteria,
    ReportSection,
    calculate_chain_yields,
    calculate_protocol_tvl_history,
    filter_eligible_vaults,
    format_return,
    format_risk_badge,
    format_sharpe,
    render_section_table,
    select_best_vaults,
    select_perp_dex_vaults,
    select_vaults_by_chain,
)
from eth_defi.vault_report.theme import DARK_THEME

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
        "trading_strategy_link": f"https://tradingstrategy.ai/trading-view/vaults/{address}",
        "vault_slug": f"vault-{address}",
        "strategy_tags": ["lending"],
    }
    record.update(overrides)
    return record


@pytest.fixture()
def vault_records() -> list[dict]:
    """Synthetic vaults covering each filter rule."""
    return [
        make_vault_record("0xaa", one_month_cagr_net=0.20),
        make_vault_record("0xbb", one_month_cagr_net=None, one_month_cagr=0.15),
        make_vault_record("0xcc", one_month_cagr_net=0.50, risk="Blacklisted"),
        make_vault_record("0xdd", one_month_cagr_net=0.60, end_date="2026-08-01T00:00:00"),
        make_vault_record("0xee", one_month_cagr_net=0.90, current_nav=50_000.0),
        make_vault_record("0xff", chain="Hypercore", protocol="Hyperliquid", protocol_slug="hyperliquid", one_month_cagr_net=100.0, one_month_returns=0.9, event_count=2, flags=["perp_dex_trading_vault"], three_months_volatility=0.8),
        make_vault_record("0x11", chain="Hypercore", protocol="Hyperliquid", protocol_slug="hyperliquid", one_month_cagr_net=100.0, one_month_returns=1.5, event_count=2, flags=["perp_dex_trading_vault"], three_months_volatility=0.8),
        make_vault_record("0x22", chain="Base", one_month_cagr_net=0.05, current_nav=3_000_000.0, years=0.1),
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
    monkeypatch.setattr(report_module, "fetch_chain_logo_uri", lambda chain, cache_dir: None)
    monkeypatch.setattr(report_module, "fetch_available_sparklines", lambda vault_ids: set(list(vault_ids)[:1]))


def test_filter_and_rank_sections(vaults_df: pd.DataFrame):
    """Blacklisted, stale and perp DEX vaults are handled and ranking prefers net returns."""
    criteria = ReportCriteria()
    eligible = filter_eligible_vaults(vaults_df, DATA_END_AT, criteria)
    assert set(eligible["address"]) == {"0xaa", "0xbb", "0xee", "0xff", "0x11", "0x22"}

    best = select_best_vaults(eligible, criteria)
    assert list(best["address"]) == ["0xaa", "0xbb", "0x22"]

    # Tied capped annualised returns are ranked by the absolute monthly return
    perp = select_perp_dex_vaults(eligible, criteria)
    assert list(perp["address"]) == ["0x11", "0xff"]

    by_chain = select_vaults_by_chain(eligible, ReportCriteria(chain_top_n=1))
    assert list(by_chain["address"]) == ["0x22", "0xaa", "0x11"]


def test_calculate_chain_yields(vaults_df: pd.DataFrame):
    """Chain yields are TVL-weighted and exclude outliers and volatile vaults."""
    criteria = ReportCriteria(chain_yield_min_chain_tvl=0)
    eligible = filter_eligible_vaults(vaults_df, DATA_END_AT, criteria)
    yields = calculate_chain_yields(eligible, criteria)
    assert "Hypercore" not in yields.index
    # Ethereum: 0.20 @ 1M, 0.15 @ 1M, 0.90 @ 50k
    assert yields.loc["Ethereum", "avg_return"] == pytest.approx((0.20 * 1_000_000 + 0.15 * 1_000_000 + 0.90 * 50_000) / 2_050_000)
    assert yields.loc["Ethereum", "vault_count"] == 3


def test_formatting():
    """Net returns are preferred and extreme Sharpe ratios are capped."""
    assert format_return(0.1234, 0.2) == "12.3% (n)"
    assert format_return(None, 0.0) == "0.0% (g)"
    assert format_return(None, None) == "---"
    assert format_return(100.0, None) == ">9,999% (n)"
    assert format_return(99.0, None) == "9,900.0% (n)"
    assert format_sharpe(8_205_524.0) == ">100"
    assert format_sharpe(float("nan")) == "---"


def test_daily_prices_and_rolling_returns(prices_path: Path):
    """Share prices are read without the pandas index and rolling returns use the window start price."""
    prices = read_vault_share_prices(prices_path, ["1-0xaa", "1-0xbb"])
    assert set(prices["id"]) == {"1-0xaa", "1-0xbb"}
    daily = calculate_daily_share_prices(prices)
    assert list(daily.columns) == ["1-0xaa", "1-0xbb"]

    rolling = calculate_rolling_returns(daily, window=datetime.timedelta(days=90))
    last = daily.index[-1]
    expected = (daily.loc[last, "1-0xaa"] / daily.loc[last - pd.Timedelta(days=90), "1-0xaa"] - 1) * 100
    assert rolling.loc[last, "1-0xaa"] == pytest.approx(expected)
    # Before a full window, returns are since inception
    assert rolling["1-0xaa"].iloc[0] == pytest.approx(0)

    # Two series get the glow style: an underlay and a line each
    fig = create_rolling_returns_figure(rolling, {"1-0xaa": "A", "1-0xbb": "B"}, DARK_THEME)
    assert len(fig.data) == 4
    fig = create_correlation_figure(daily, {"1-0xaa": "A", "1-0xbb": "B"}, DARK_THEME)
    assert fig.data[0].z.shape == (2, 2)


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
        criteria=ReportCriteria(correlation_min_tvl=0),
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
    assert manifest["sections"]["best"] == 3
    assert manifest["sections"]["perp_dex"] == 2
    assert (tmp_path / "out" / "tables" / "best.csv").exists()
    assert manifest["rankings"]["best"] == ["1-0xaa", "1-0xbb", "1-0x22"]
    assert read_previous_ranking(tmp_path / "out") == ["1-0xaa", "1-0xbb", "1-0x22"]
    assert "3 of the 3 stablecoin yield vaults with at least $200k TVL beat the 3-month US Treasury bill yield of 4.0%" in post_html
    assert "vault-sparklines.tradingstrategy.ai" in post_html

    # An existing draft is checked before any chart is uploaded
    report.chart_paths = {"best_rolling": tmp_path / "missing.png"}
    client = GhostAdminClient("https://example.ghost.io", "key:" + "00" * 32)
    client.session = FakeSession("draft")
    with pytest.raises(GhostAPIError):
        publish_report_draft(report, client)
    assert all(method == "GET" for method, _ in client.session.calls)


@pytest.mark.skipif(not CHOREOGRAPHER_CHROME_PATH.exists(), reason="Kaleido needs Chrome, install with plotly_get_chrome")
def test_render_report_charts(tmp_path: Path, vaults_df: pd.DataFrame, prices_path: Path):
    """All charts render as branded PNG panels, and the hero image has the social card size."""
    previous_table = '<h2 id="the-best-performing-vaults">Best</h2><table><tbody><tr><td><a href="https://tradingstrategy.ai/trading-view/vaults/vault-0xbb">B</a></td></tr><tr><td><a href="https://tradingstrategy.ai/trading-view/vaults/vault-0xaa">A</a></td></tr></tbody></table>'
    previous = GhostPost(id="p0", title="Previous", slug="previous", status="published", published_at=datetime.datetime(2026, 8, 25), updated_at=None, html=previous_table)
    data = VaultReportData(vaults_df=vaults_df, prices_path=prices_path)
    report = generate_monthly_vault_report(data, output_dir=tmp_path / "out", criteria=ReportCriteria(correlation_min_tvl=0, chain_yield_min_chain_tvl=0), previous=previous)
    assert set(report.chart_paths) == {"chain_yields", "protocol_tvl", "best_rolling", "low_volatility_rolling", "risk_return", "movers", "correlation"}
    for path in report.chart_paths.values():
        image = Image.open(path)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0  # Rounded panel corner is transparent
    assert Image.open(report.hero_path).size == HERO_SIZE
    assert Image.open(tmp_path / "out" / "hero-square.png").size == SQUARE_HERO_SIZE
    assert 'src="charts/best_rolling.png"' in (tmp_path / "out" / "post.html").read_text()


@pytest.mark.skipif(not CHOREOGRAPHER_CHROME_PATH.exists(), reason="Kaleido needs Chrome, install with plotly_get_chrome")
def test_render_report_charts_without_prices(tmp_path: Path, vaults_df: pd.DataFrame):
    """Missing price history leaves out the price charts instead of aborting the report."""
    empty_prices = tmp_path / "empty.parquet"
    pd.DataFrame({"id": ["1-0xother"], "timestamp": [pd.Timestamp(DATA_END_AT)], "share_price": [1.0], "total_assets": [1.0]}).to_parquet(empty_prices)
    data = VaultReportData(vaults_df=vaults_df, prices_path=empty_prices)
    report = generate_monthly_vault_report(data, output_dir=tmp_path / "out", criteria=ReportCriteria(correlation_min_tvl=0, chain_yield_min_chain_tvl=0))
    assert set(report.chart_paths) == {"chain_yields", "risk_return"}
    assert "best" in report.context.tables


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
    """Daily accrual over the rolling window matches compounding."""
    yields = pd.Series(0.0365, index=pd.date_range("2026-01-02", periods=200, freq="B"))
    index = pd.date_range("2026-06-01", "2026-06-30", freq="D")
    rolling = calculate_treasury_bill_rolling_returns(yields, datetime.timedelta(days=90), index)
    assert rolling.iloc[-1] == pytest.approx(((1 + 0.0365 / 365) ** 90 - 1) * 100)


def test_movers(vaults_df: pd.DataFrame):
    """Previous table links resolve by address or slug, and ranks are recalculated in the current universe."""
    post_html = '<h2 id="the-best-performing-vaults">Best</h2><table><tbody><tr><td><a href="https://tradingstrategy.ai/trading-view/hypercore/vaults/x?a=0xFF&amp;ref=x">Perp</a></td></tr><tr><td><a href="https://tradingstrategy.ai/trading-view/ethereum/vaults/old-name?a=0xbb">B</a></td></tr><tr><td><a href="https://tradingstrategy.ai/trading-view/vaults/vault-0x22">C</a></td></tr><tr><td><a href="https://tradingstrategy.ai/trading-view/vaults/gone">Gone</a></td></tr></tbody></table>'
    links = parse_ranked_vault_links(post_html, "the-best-performing-vaults")
    assert len(links) == 4
    previous_ids = [resolve_vault_id(link, vaults_df) for link in links]
    assert previous_ids == ["1-0xff", "1-0xbb", "1-0x22", None]

    # A slug shared by vaults on two chains resolves only with the chain in the link
    twin = vaults_df.loc[["1-0x22"]].assign(id="42161-0x22", chain="Arbitrum")
    twin.index = ["42161-0x22"]
    with_twin = pd.concat([vaults_df.assign(vault_slug=vaults_df["vault_slug"].replace("vault-0x22", "shared")), twin.assign(vault_slug="shared")])
    assert resolve_vault_id("https://tradingstrategy.ai/trading-view/vaults/shared", with_twin) is None
    assert resolve_vault_id("https://tradingstrategy.ai/trading-view/base/vaults/shared", with_twin) == "1-0x22"
    assert resolve_vault_id("https://tradingstrategy.ai/trading-view/arbitrum/vaults/shared", with_twin) == "42161-0x22"

    changes, dropped = calculate_rank_changes(previous_ids, ["1-0xaa", "1-0x22", "1-0xbb"], top_n=2)
    assert [(c.vault_id, c.previous_rank, c.status) for c in changes] == [("1-0xaa", None, "new"), ("1-0x22", 2, "same")]
    assert dropped == ["1-0xbb"]


def test_protocol_tvl_history(vaults_df: pd.DataFrame, prices_path: Path):
    """Weekly TVL is summed per protocol with the tail grouped as Other."""
    tvl = read_vault_tvl_history(prices_path, list(vaults_df.index), start_at=DATA_END_AT - datetime.timedelta(days=60))
    by_protocol = calculate_protocol_tvl_history(tvl, vaults_df, top_n=1)
    assert list(by_protocol.columns) == ["Morpho", "Other"]
    assert by_protocol.iloc[-1].sum() == pytest.approx(tvl.iloc[-1].sum())


def test_table_badges_and_sparklines(vaults_df: pd.DataFrame):
    """Tables show risk pills and sparklines only for vaults that have one."""
    assert ">Low<" in format_risk_badge("Low")
    assert ">Unrated<" in format_risk_badge(None)
    assert "<a " not in format_risk_badge(None)
    table = render_section_table(ReportSection(vaults_df.loc[["1-0xaa", "1-0xbb"]], sparkline_ids=frozenset({"1-0xaa"})))
    assert table.count("sparkline-90d-") == 1
    assert "sparkline-90d-1-0xaa.png" in table
    assert table.startswith("<table>")
    assert "<span" in format_risk_badge(None)  # Unrated is muted text, not a pill link


def test_compose_chart_panel(tmp_path: Path):
    """The branded panel adds a header and footer and has transparent rounded corners."""
    chart = tmp_path / "chart.png"
    Image.new("RGB", (1400, 800), DARK_THEME.surface).save(chart)
    output = compose_chart_panel(chart, DARK_THEME, "Title", "Subtitle", "Data 2026-09-25", "tradingstrategy.ai", tmp_path / "panel.png")
    image = Image.open(output)
    assert image.size == (1400, 800 + 128 + 84)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((700, 500))[3] == 255

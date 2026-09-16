"""Generate the Tessera propAMM slippage tutorial notebook.

The notebook ``docs/source/tutorials/tessera-propamm-slippage.ipynb`` is
generated from this script so that its many sections stay consistent and
diffable. Edit here, then::

    poetry run python scripts/base/build-tessera-notebook.py
    source .local-test.env && poetry run jupyter execute docs/source/tutorials/tessera-propamm-slippage.ipynb --inplace --timeout=1500
"""

from pathlib import Path

import nbformat as nbf

nb = nbf.v4.new_notebook()
cells = []


def md(text: str) -> None:
    cells.append(nbf.v4.new_markdown_cell(text.strip()))


def code(text: str) -> None:
    cells.append(nbf.v4.new_code_cell(text.strip()))


md("""
# Tessera propAMM: how retail gets worse fills on Base

- In this notebook, we analyse execution quality on [Tessera](https://defillama.com/protocol/tessera-v), the Wintermute proprietary AMM (propAMM) that has become one of the largest trading venues on Base
- PropAMMs post quotes onchain from an off-chain pricing engine and are routed to by aggregators (OKX, KyberSwap, 1inch, 0x, Paraswap, Binance Wallet…) because their quoted prices beat AMM pools
- The [0x "PropAMM Shenanigans" post](https://0x.org/post/propamm-shenanigans) documented that the quoted price does not survive until settlement: the operator refreshes a tight quote in the last Flashblock of block N, aggregators route on it, then reprices worse in the first Flashblock of block N+1 where user transactions settle, and the user's slippage tolerance silently absorbs the difference
- We measure this over ten months of onchain history and answer four questions
  1. **Is the quote honest?** Does the price an aggregator routed on survive until the fill?
  2. **Who pays?** Retail vs bots, by aggregator and by front-end
  3. **How much?** In basis points against Tessera's own quote and against a fair reference price from the deepest Uniswap V3 / Aerodrome pool, and in dollars
  4. **When, and why?** The skim comes and goes in regimes; we detect them, test whether the keeper's onchain behaviour switches with them, measure what the market does after retail and bot fills, and look at who ends up paying at the wallet level
- In between we look for the mechanism: *why* retail fills are worse than bot fills on the same venue in the same blocks

## Usage

This is an open source notebook based on open data
- You can edit and remix this notebook yourself

To do your own data research:

- Read general instructions [how to run the tutorials](./)
- Run `scripts/base/scan-tessera-propamm.py` to collect Tessera trades, keeper price updates, transaction traces (aggregator identification, user slippage bounds) and historical quotes into a DuckDB file
- Run `scripts/base/scan-tessera-benchmark-prices.py` to add reference pool prices for a fair-price comparison
- The default database location is `~/.tradingstrategy/tessera/tessera-base.duckdb`; override with `TESSERA_DUCKDB_PATH`

Terminology used below:

- **Quote**: Tessera's own `tesseraSwapViewAmounts()` output for the trade size at the end of the previous block — the best onchain approximation of what the aggregator routed on
- **Fill**: the amounts in the `TesseraTrade` event
- **Quote-to-fill bps**: `(quoted_out − filled_out) / quoted_out × 10 000`; positive means the user got less than quoted
- **Fair price**: the marginal (mid) price of the deepest concentrated-liquidity pool for the pair at the end of the previous block
- **Retail**: trades routed through a labelled aggregator router; **bot**: trades from known arbitrage contracts or EIP-7702 self-calls; **unlabelled**: routers we could not identify
- **In-block position**: `cumulative_gas_used / block_gas_used` of the transaction, 0 = top of block, 1 = bottom; Base blocks are built from ten 200 ms Flashblocks in order, so this is the proxy for the Flashblock index
""")

md("""
## Setup

- Set up notebook rendering output mode
- Use static image charts so this notebook is readable on Github / ReadTheDocs
- Colours are assigned by role and kept fixed across every chart: retail is blue, bots are orange, unlabelled flow is grey, keeper price updates are aqua
""")

code("""
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import init_notebook_mode
from plotly.subplots import make_subplots

from eth_defi.research.notebook import set_large_plotly_chart_font

pd.options.display.float_format = "{:,.2f}".format
pd.options.display.max_columns = None
pd.options.display.max_rows = 60

image_format = "png"
width = 1400
height = 700

init_notebook_mode()
pio.renderers.default = image_format
current_renderer = pio.renderers[image_format]
current_renderer.width = width
current_renderer.height = height

set_large_plotly_chart_font(title_font_size=26, font_size=18, legend_font_size=18, axis_title_font_size=20, line_width=2)

# Fixed role colours, never cycled
COLOURS = {
    "retail": "#2a78d6",
    "bot": "#eb6834",
    "unlabelled": "#8a8a8a",
    "price update": "#1baf7a",
    "quote": "#4a3aa7",
    "fill": "#e34948",
}
FLOW_ORDER = ["retail", "bot", "unlabelled"]

# Fixed aggregator colour order (validated categorical palette, slots 1-8)
AGGREGATOR_COLOURS = {
    "okx": "#2a78d6",
    "kyberswap": "#eb6834",
    "0x": "#1baf7a",
    "1inch": "#eda100",
    "paraswap": "#e87ba4",
    "binance-wallet": "#008300",
    "lifi": "#4a3aa7",
    "aggregator-2f68": "#e34948",
}


MAJORS = ["WETH/USDC", "USDC/WETH", "cbBTC/USDC", "USDC/cbBTC"]


def detect_change_points(series: pd.Series, min_size: int = 5, min_shift: float = 2.0) -> list[pd.Timestamp]:
    \"\"\"Optimal partitioning of a daily series with a sum-of-absolute-deviations cost.

    Exact dynamic programme (not greedy binary segmentation, which cannot isolate a short
    excursion in the middle of a series): minimises the total within-segment absolute deviation
    from the segment median plus a penalty of ``min_shift * min_size`` per extra segment, with
    every segment at least ``min_size`` observations. Adjacent segments whose medians differ by
    less than ``min_shift`` are merged afterwards. Missing days must already be dropped by the caller.
    \"\"\"
    values = series.dropna()
    v = values.values
    n = len(v)
    penalty = min_shift * min_size
    if n < 2 * min_size:
        return []

    def cost(i: int, j: int) -> float:
        seg = v[i:j]
        return float(np.abs(seg - np.median(seg)).sum())

    best = [np.inf] * (n + 1)
    prev = [-1] * (n + 1)
    best[0] = -penalty
    for j in range(min_size, n + 1):
        for i in range(0, j - min_size + 1):
            if best[i] == np.inf:
                continue
            c = best[i] + cost(i, j) + penalty
            if c < best[j]:
                best[j], prev[j] = c, i
    cuts: list[int] = []
    j = n
    while prev[j] > 0:
        cuts.append(prev[j])
        j = prev[j]
    cuts = sorted(cuts)

    changed = True
    while changed and cuts:
        changed = False
        edges = [0] + cuts + [n]
        for k in range(1, len(edges) - 1):
            if abs(np.median(v[edges[k - 1] : edges[k]]) - np.median(v[edges[k] : edges[k + 1]])) < min_shift:
                cuts.pop(k - 1)
                changed = True
                break
    return [values.index[k] for k in cuts]


def build_regimes(daily: pd.Series, cuts: list[pd.Timestamp], skim_threshold_bps: float = 3.0) -> pd.DataFrame:
    \"\"\"Turn change points into a regimes table. Positive medians mean users received less than fair.\"\"\"
    edges = [daily.index.min()] + list(cuts) + [daily.index.max() + pd.Timedelta(days=1)]
    rows = []
    for i, (start, end) in enumerate(zip(edges[:-1], edges[1:])):
        seg = daily[(daily.index >= start) & (daily.index < end)]
        rows.append({"regime_id": i, "start": start, "end": end - pd.Timedelta(days=1), "days": len(seg), "retail_fill_vs_fair_p50": seg.median(), "label": "skim" if seg.median() >= skim_threshold_bps else "honest"})
    return pd.DataFrame(rows)


def assign_regime(ts: pd.Series, regimes: pd.DataFrame) -> pd.Series:
    \"\"\"Map timestamps to regime ids by date range.\"\"\"
    out = pd.Series(np.nan, index=ts.index)
    for _, r in regimes.iterrows():
        out[(ts >= r["start"]) & (ts < r["end"] + pd.Timedelta(days=1))] = r["regime_id"]
    return out


def add_regime_bands(fig: go.Figure, regimes: pd.DataFrame) -> go.Figure:
    \"\"\"Shade skim regimes and label every regime at the top of a time-series chart.\"\"\"
    for _, r in regimes.iterrows():
        x1 = r["end"] + pd.Timedelta(days=1)
        if r["label"] == "skim":
            fig.add_vrect(x0=r["start"], x1=x1, fillcolor=COLOURS["fill"], opacity=0.08, line_width=0, layer="below")
        fig.add_annotation(x=r["start"] + (x1 - r["start"]) / 2, y=1.0, yref="paper", text=f"{r['label']} {r['retail_fill_vs_fair_p50']:+.1f} bps", showarrow=False, yanchor="bottom", font=dict(size=14))
    return fig


def style(fig: go.Figure, title: str, xaxis: str, yaxis: str, legend: bool = True) -> go.Figure:
    \"\"\"Apply the shared chart style: one y axis, recessive grid, legend only when needed.\"\"\"
    fig.update_layout(
        title=title,
        xaxis_title=xaxis,
        yaxis_title=yaxis,
        template="plotly_white",
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, traceorder="normal"),
        margin=dict(l=70, r=30, t=110, b=70),
        bargap=0.15,
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#e6e6e3", zeroline=False)
    return fig
""")

md("""
## Open the dataset

- Connect to the DuckDB file produced by the two scanner scripts, read-only
- The raw tables cover the full Tessera history on Base; the enrichment tables (traces, quotes, benchmarks) cover the analysis window from block 48,000,000 (July 2026 onwards)
- Show what we have
""")

code("""
duckdb_path = Path(os.environ.get("TESSERA_DUCKDB_PATH", "~/.tradingstrategy/tessera/tessera-base.duckdb")).expanduser()
assert duckdb_path.exists(), f"Run scripts/base/scan-tessera-propamm.py first, {duckdb_path} missing"
con = duckdb.connect(str(duckdb_path), read_only=True)
con.execute("SET enable_progress_bar = false")

WINDOW_START_BLOCK = int(os.environ.get("TESSERA_WINDOW_START_BLOCK", "48000000"))
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"

tables = ["blocks", "trades", "price_updates", "trade_calls", "trade_orders", "quotes", "benchmark_swaps", "trade_benchmarks"]
overview = pd.DataFrame(
    [(t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in tables],
    columns=["Table", "Rows"],
)
history = con.execute("SELECT min(timestamp), max(timestamp), min(block_number), max(block_number) FROM trades").fetchone()
window = con.execute("SELECT min(timestamp), max(timestamp) FROM trades WHERE block_number >= ?", [WINDOW_START_BLOCK]).fetchone()
print(f"Full history: {history[0]:%Y-%m-%d} – {history[1]:%Y-%m-%d} (blocks {history[2]:,} – {history[3]:,})")
print(f"Analysis window: {window[0]:%Y-%m-%d} – {window[1]:%Y-%m-%d} (block {WINDOW_START_BLOCK:,} onwards)")
display(overview.style.format({"Rows": "{:,}"}))
""")

md("""
## Load trades in the analysis window

- Join the per-trade analysis view with the fair-price benchmark view
- Classify flow: `retail` is anything routed through a labelled aggregator router, `bot` is a known arbitrage contract or an EIP-7702 self-call, `unlabelled` is a router we could not identify
- Compute the USD notional from the USDC leg of the trade, and the dollar value of the quote-to-fill gap
""")

code("""
df = con.execute(\"\"\"
    SELECT
        a.block_number, a.timestamp, a.tx_hash, a.log_index, a.tx_index,
        a.token_in, a.token_out, a.symbol_in, a.symbol_out,
        a.amount_in_decimal, a.amount_out_decimal,
        a.aggregator, a.frontend, a.wallet_kind, a.client_data, a.is_self_call, a.router, a.tx_from,
        a.rel_gas_position, a.max_priority_fee_per_gas,
        a.quote_to_fill_bps, a.same_block_quote_to_fill_bps, a.size_impact_bps,
        a.user_slippage_bps, a.user_slippage_consumed_fraction, a.user_headroom_bps, a.user_route_headroom_bps,
        a.given_slippage_bps, a.order_matches_leg,
        b.fill_vs_benchmark_bps, b.quote_vs_benchmark_bps, b.benchmark_age_blocks, b.protocol AS benchmark_protocol, b.benchmark_fee_bps,
        b.pool, b.sells_token0, b.fill_price_t1_per_t0
    FROM trade_analysis a
    LEFT JOIN trade_vs_benchmark b ON b.tx_hash = a.tx_hash AND b.log_index = a.log_index
    WHERE a.venue = 'tessera' AND a.block_number >= ?
\"\"\", [WINDOW_START_BLOCK]).df()

df["pair"] = df["symbol_in"] + "/" + df["symbol_out"]
df["flow"] = np.select(
    [(df["aggregator"] == "bot") | df["is_self_call"].fillna(False), df["aggregator"].notna()],
    ["bot", "retail"],
    default="unlabelled",
)
df["notional_usd"] = np.where(df["token_in"] == USDC, df["amount_in_decimal"], np.where(df["token_out"] == USDC, df["amount_out_decimal"], np.nan))
df["extracted_usd"] = df["quote_to_fill_bps"] / 10_000 * df["notional_usd"]
df["hour"] = df["timestamp"].dt.floor("h")
df["day"] = df["timestamp"].dt.floor("D")

summary = df.groupby("flow").agg(
    trades=("tx_hash", "size"),
    notional_usd=("notional_usd", "sum"),
    median_quote_to_fill_bps=("quote_to_fill_bps", "median"),
    has_user_bound=("user_slippage_bps", lambda s: s.notna().mean()),
).reindex(FLOW_ORDER)
summary["share_of_trades"] = summary["trades"] / summary["trades"].sum()
display(summary.style.format({"trades": "{:,}", "notional_usd": "${:,.0f}", "median_quote_to_fill_bps": "{:.2f}", "has_user_bound": "{:.0%}", "share_of_trades": "{:.0%}"}))
""")

md("""
## Dataset overview: pairs and volume

- Tessera volume is concentrated in a handful of pairs against USDC
- Retail and bot flow have very different pair preferences: bots concentrate on WETH/USDC and cbBTC/USDC where the reference price is tightest
""")

code("""
pairs = df.groupby(["pair", "flow"]).agg(trades=("tx_hash", "size"), notional_usd=("notional_usd", "sum")).reset_index()
pair_order = pairs.groupby("pair")["notional_usd"].sum().sort_values(ascending=False).index[:10]
pairs = pairs[pairs["pair"].isin(pair_order)]

fig = go.Figure()
for flow in FLOW_ORDER:
    sub = pairs[pairs["flow"] == flow].set_index("pair").reindex(pair_order)
    fig.add_bar(name=flow, x=list(pair_order), y=sub["notional_usd"] / 1e6, marker_color=COLOURS[flow])
fig.update_layout(barmode="stack")
style(fig, "Tessera volume by pair and flow type, analysis window", "Pair (token in / token out)", "Volume, USD millions").show()

table = df.pivot_table(index="pair", columns="flow", values="notional_usd", aggfunc="sum", fill_value=0).reindex(pair_order)
table["total"] = table.sum(axis=1)
display((table / 1e6).style.format("${:,.1f}M"))
""")

md("""
# Question 1: is the quote honest?

## Where in the block does the keeper update prices?

- Tessera's pricing engine reads quotes from a store contract that the operator's keeper wallet writes to several times per block, with no events emitted
- If updates were driven by market data they would be spread evenly through the block
- Instead they cluster at the very top and the very bottom of the block: the 0x post's "quote in the last Flashblock, reprice in the first Flashblock" signature, here over the whole analysis window
""")

code("""
updates = con.execute(\"\"\"
    SELECT floor(rel_gas_position * 20) / 20 AS position_bin, count(*) AS n
    FROM price_update_analysis
    WHERE selector = '0x1667d875' AND block_number >= ? AND rel_gas_position IS NOT NULL
    GROUP BY 1 ORDER BY 1
\"\"\", [WINDOW_START_BLOCK]).df()
updates["share"] = updates["n"] / updates["n"].sum()

fig = go.Figure(go.Bar(x=updates["position_bin"] + 0.025, y=updates["share"], width=0.045, marker_color=COLOURS["price update"], name="price updates"))
top = updates.loc[updates["position_bin"] < 0.1, "share"].sum()
bottom = updates.loc[updates["position_bin"] >= 0.8, "share"].sum()
fig.add_annotation(x=0.05, y=updates["share"].max(), text=f"first 10% of block: {top:.0%}", showarrow=False, yshift=18, xanchor="left")
fig.add_annotation(x=0.9, y=updates.loc[updates["position_bin"] >= 0.8, "share"].max(), text=f"last 20% of block: {bottom:.0%}", showarrow=False, yshift=18, xanchor="right")
style(fig, f"Keeper price updates by position in block ({updates['n'].sum():,} updates)", "Position in block (0 = top, 1 = bottom, by cumulative gas)", "Share of updates", legend=False)
fig.update_yaxes(tickformat=".0%")
fig.show()
""")

md("""
## Quote survival: how much worse is the fill than the quote?

- For every trade we recomputed Tessera's own quote for the exact trade size at the end of the previous block, the moment an aggregator would have snapshotted it
- Positive quote-to-fill bps means the user received less than that quote
- Retail flow pays a consistent few basis points; bots fill at or better than the quote
- The retail curve has steps at exactly 0, 1, 2 and 4 bps: the degradation is quantised in whole basis points, a discrete pricing parameter rather than continuous market noise
""")

code("""
fig = go.Figure()
for flow in FLOW_ORDER:
    s = df.loc[df["flow"] == flow, "quote_to_fill_bps"].dropna().clip(-10, 30).sort_values()
    fig.add_scatter(x=s.values, y=np.linspace(0, 1, len(s)), mode="lines", name=f"{flow} (median {s.median():.1f} bps)", line=dict(color=COLOURS[flow], width=2))
fig.add_vline(x=0, line=dict(color="#b0b0ad", dash="dot"))
style(fig, "Quote-to-fill degradation, cumulative distribution", "Fill worse than quote, bps (positive = user got less)", "Share of trades")
fig.update_yaxes(tickformat=".0%")
fig.show()

q = df.groupby("flow")["quote_to_fill_bps"].describe(percentiles=[0.1, 0.5, 0.9, 0.99]).reindex(FLOW_ORDER)[["count", "mean", "10%", "50%", "90%", "99%"]]
display(q.style.format("{:,.2f}"))
""")

md("""
## Quote survival by pair

- The degradation is not a property of one illiquid token: it is present on WETH/USDC and cbBTC/USDC, the deepest markets on Base
- The bottom of the distribution (p10) sits around one basis point on the majors — a spread-like floor — while the median is five to six
""")

code("""
retail = df[df["flow"] == "retail"]
by_pair = retail.groupby("pair")["quote_to_fill_bps"].agg(n="size", p10=lambda s: s.quantile(0.1), p50="median", p90=lambda s: s.quantile(0.9), mean="mean")
by_pair = by_pair[by_pair["n"] >= 1000].sort_values("n", ascending=False).head(10)

fig = go.Figure()
fig.add_bar(x=by_pair.index, y=by_pair["p50"], name="median", marker_color=COLOURS["retail"])
fig.add_scatter(x=by_pair.index, y=by_pair["p90"], mode="markers", name="p90", marker=dict(color=COLOURS["fill"], size=10, symbol="diamond"))
fig.add_scatter(x=by_pair.index, y=by_pair["p10"], mode="markers", name="p10", marker=dict(color=COLOURS["quote"], size=10, symbol="triangle-up"))
style(fig, "Retail quote-to-fill degradation by pair", "Pair", "bps, positive = user got less than quoted").show()
display(by_pair.style.format({"n": "{:,}", "p10": "{:.2f}", "p50": "{:.2f}", "p90": "{:.2f}", "mean": "{:.2f}"}))
""")

md("""
## Price movement inside a block vs between blocks

- In a normal market the price moves more between blocks (two seconds apart) than inside a block (200 ms Flashblocks)
- 0x reported the inverse for propAMMs, sampling live at Flashblock granularity: five to seven times more movement within the block than across blocks
- Quotes are only comparable at the same trade size, so intra-block movement uses each trade's own quote at the end of the previous block vs the end of its own block, and inter-block movement uses WETH→USDC trades in consecutive blocks whose sizes are within 5% of each other; the reference pool's mid between consecutive blocks shows how much the market itself moved
- With end-of-block snapshots we do **not** see the inversion: Tessera's quote moves about as much as the market does. Both of our snapshots fall in the tight, end-of-block state; the worse mid-block state that retail settles in (see the mechanism section) sits between them and is invisible to historical `eth_call`. Reproducing 0x's measurement needs live Flashblock sampling
""")

code("""
weth_sells = df[(df["token_in"] == WETH) & (df["token_out"] == USDC)].dropna(subset=["quote_to_fill_bps", "same_block_quote_to_fill_bps"]).copy()
# Same trade, same size: end of previous block vs end of own block
weth_sells["intra_bps"] = ((1 - weth_sells["same_block_quote_to_fill_bps"] / 1e4) / (1 - weth_sells["quote_to_fill_bps"] / 1e4) - 1) * 1e4
intra = weth_sells["intra_bps"].replace([np.inf, -np.inf], np.nan).dropna()
intra = intra[intra.abs() < 100]

# Consecutive blocks, sizes within 5%: quoted price per WETH at end of previous block
s1 = weth_sells.sort_values(["block_number", "amount_in_decimal"]).drop_duplicates("block_number")
s1["quote_px"] = (1 - s1["quote_to_fill_bps"] / 1e4) * s1["amount_out_decimal"] / s1["amount_in_decimal"]
nxt = s1.shift(-1)
consecutive = (nxt["block_number"] == s1["block_number"] + 1) & ((nxt["amount_in_decimal"] / s1["amount_in_decimal"] - 1).abs() < 0.05)
inter = ((nxt["quote_px"] / s1["quote_px"] - 1) * 1e4)[consecutive].dropna()
inter = inter[inter.abs() < 100]

market = con.execute(\"\"\"
    WITH p AS (
        SELECT block_number, price, lag(price) OVER (ORDER BY block_number) AS prev_price, lag(block_number) OVER (ORDER BY block_number) AS prev_block
        FROM benchmark_block_prices b JOIN benchmark_pools p ON p.pool = b.pool
        WHERE p.token0 = ? AND p.token1 = ? AND b.block_number >= ?
    )
    SELECT (price / prev_price - 1) * 1e4 AS bps FROM p WHERE block_number = prev_block + 1
\"\"\", [WETH, USDC, WINDOW_START_BLOCK]).df()["bps"].dropna()

labels = ["Tessera quote, within block", "Tessera quote, between blocks", "Reference pool mid, between blocks"]
values = [intra.abs().median(), inter.abs().median(), market.abs().median()]
fig = go.Figure(go.Bar(x=labels, y=values, marker_color=[COLOURS["fill"], COLOURS["quote"], COLOURS["unlabelled"]]))
style(fig, "WETH/USDC price movement: median absolute change", "", "bps", legend=False).show()
display(pd.DataFrame({"median |Δ| bps": values, "p90 |Δ| bps": [intra.abs().quantile(0.9), inter.abs().quantile(0.9), market.abs().quantile(0.9)], "samples": [len(intra), len(inter), len(market)]}, index=labels).style.format("{:,.2f}"))
""")

md("""
## Quote and fill vs fair price over time

- Is the skim episodic or steady? Six-hour medians over the window for the majors with a fresh reference price (buckets with fewer than 50 trades dropped)
- Retail fills sit five to seven bps below fair through almost the whole window. The exception is a short spell around 23–27 July when retail was filled within about one bp of fair; the venue can evidently fill retail at its quote when it chooses to
- Bot fills (shown only where a bucket has at least 50 bot trades) are absent before late July, appear in force from 17 August, and sit slightly above fair
""")

code("""
fresh = df[df["pair"].isin(["WETH/USDC", "USDC/WETH", "cbBTC/USDC", "USDC/cbBTC"]) & (df["benchmark_age_blocks"] <= 3)]
fresh = fresh.assign(bucket=fresh["timestamp"].dt.floor("6h"))

def bucket_median(frame: pd.DataFrame, column: str, minimum: int = 50) -> pd.Series:
    \"\"\"Median per 6-hour bucket, dropping buckets with too few trades to be meaningful.\"\"\"
    g = frame.groupby("bucket")[column]
    return g.median()[g.size() >= minimum]

hourly_quote = bucket_median(fresh, "quote_vs_benchmark_bps")
hourly_retail = bucket_median(fresh[fresh["flow"] == "retail"], "fill_vs_benchmark_bps")
hourly_bot = bucket_median(fresh[fresh["flow"] == "bot"], "fill_vs_benchmark_bps")

fig = go.Figure()
fig.add_scatter(x=hourly_quote.index, y=hourly_quote, mode="lines+markers", name="Tessera quote vs fair (all flow)", line=dict(color=COLOURS["quote"], width=1.5), marker=dict(size=5))
fig.add_scatter(x=hourly_retail.index, y=-hourly_retail, mode="lines+markers", name="retail fill vs fair", line=dict(color=COLOURS["retail"], width=2), marker=dict(size=5))
fig.add_scatter(x=hourly_bot.index, y=-hourly_bot, mode="lines+markers", name="bot fill vs fair", line=dict(color=COLOURS["bot"], width=2), marker=dict(size=5))
fig.add_hline(y=0, line=dict(color="#b0b0ad", dash="dot"))
style(fig, "WETH/USDC and cbBTC/USDC: quote and fills vs reference pool mid, 6-hour medians", "", "bps better than fair (negative = worse)")
fig.show()
""")

md("""
# Question 2: who pays?

## Degradation by aggregator

- The same venue, the same blocks, but very different outcomes depending on which aggregator routed the trade
- KyberSwap and 0x flow gets fills close to the quote; OKX, 1inch, Paraswap, LI.FI and Binance Wallet flow pays the full skim
- 0x publicly monitors fill quality and cuts sources that misbehave; the numbers suggest that pressure works
""")

code("""
agg = df[df["flow"] == "retail"].groupby("aggregator")["quote_to_fill_bps"].agg(
    n="size", p10=lambda s: s.quantile(0.1), p25=lambda s: s.quantile(0.25), p50="median", p75=lambda s: s.quantile(0.75), p90=lambda s: s.quantile(0.9), mean="mean"
)
agg = agg[agg["n"] >= 500].sort_values("p50")

fig = go.Figure()
for name, row in agg.iterrows():
    label = f"{name} (n={int(row['n']):,})"
    fig.add_box(
        name=label, x=[label],
        q1=[row["p25"]], median=[row["p50"]], q3=[row["p75"]], lowerfence=[row["p10"]], upperfence=[row["p90"]],
        marker_color=AGGREGATOR_COLOURS.get(name, "#8a8a8a"), line_width=1.5,
    )
fig.add_hline(y=0, line=dict(color="#b0b0ad", dash="dot"))
style(fig, "Quote-to-fill degradation by aggregator, retail flow (box = p25–p75, whiskers = p10–p90)", "Aggregator, ordered by median", "bps, positive = user got less than quoted", legend=False).show()
display(agg[["n", "p10", "p50", "p90", "mean"]].style.format({"n": "{:,}", "p10": "{:.2f}", "p50": "{:.2f}", "p90": "{:.2f}", "mean": "{:.2f}"}))
""")

md("""
## Degradation by front-end

- Aggregator routers often carry a front-end identifier: KyberSwap's `clientData` names the integrating app, ERC-4337 flow identifies smart-wallet users (Coinbase Smart Wallet)
- This puts retail-facing brand names on the numbers
""")

code("""
import json

def frontend_label(row):
    if isinstance(row["client_data"], str) and row["client_data"].startswith("{"):
        try:
            return f"{row['aggregator']}: {json.loads(row['client_data']).get('Source') or 'unknown source'}"
        except json.JSONDecodeError:
            pass
    if row["wallet_kind"] == "erc4337":
        return f"{row['aggregator']} via smart wallet"
    if isinstance(row["frontend"], str) and row["frontend"] != row["aggregator"]:
        return f"{row['aggregator']} via {row['frontend']}"
    return f"{row['aggregator']} direct"

retail = df[df["flow"] == "retail"].copy()
retail["frontend_label"] = retail.apply(frontend_label, axis=1)
fe = retail.groupby("frontend_label").agg(n=("tx_hash", "size"), p50=("quote_to_fill_bps", "median"), p90=("quote_to_fill_bps", lambda s: s.quantile(0.9)), notional_usd=("notional_usd", "sum"))
fe = fe[fe["n"] >= 300].sort_values("p50", ascending=True)

fig = go.Figure(go.Bar(x=fe["p50"], y=fe.index, orientation="h", marker_color=COLOURS["retail"], name="median"))
style(fig, "Median quote-to-fill degradation by front-end", "bps, positive = user got less than quoted", "", legend=False)
fig.update_layout(height=max(600, 40 * len(fe) + 160))
fig.update_yaxes(tickmode="array", tickvals=list(fe.index), automargin=True)
fig.show()
display(fe.sort_values("p50", ascending=False).fillna(0).style.format({"n": "{:,}", "p50": "{:.2f}", "p90": "{:.2f}", "notional_usd": "${:,.0f}"}))
""")

md("""
## What the user allowed vs what was taken

- From the aggregator router calldata we decoded the minimum output each user accepted; relative to Tessera's quote this is their slippage tolerance in bps
- Tolerances cluster at front-end defaults of 50, 100 and 200 bps, twenty to forty times the median skim, so the fill never reverts and nothing looks wrong to the user
- The degradation taken does not grow with the tolerance given: the venue takes a fixed few bps, not "whatever the user allowed"
- Only trades where the router order maps onto this single Tessera leg are shown (multi-hop routes express the bound in a different token)
""")

code("""
bound = df[(df["flow"] == "retail") & df["order_matches_leg"].fillna(False) & df["user_slippage_bps"].between(0, 1000)].copy()
tol_bins = [0, 25, 75, 150, 250, 400, 1000]
tol_labels = ["≤25 bps", "~50 bps", "~100 bps", "~200 bps", "~300 bps", ">400 bps"]
bound["tolerance_bucket"] = pd.cut(bound["user_slippage_bps"], bins=tol_bins, labels=tol_labels, include_lowest=True)
tol = bound.groupby("tolerance_bucket", observed=True).agg(n=("tx_hash", "size"), degradation_p50=("quote_to_fill_bps", "median"), degradation_p90=("quote_to_fill_bps", lambda s: s.quantile(0.9)), consumed_p50=("user_slippage_consumed_fraction", "median"))
tol["share_of_trades"] = tol["n"] / tol["n"].sum()

fig = go.Figure(go.Bar(x=tol.index.astype(str), y=tol["share_of_trades"], marker_color=COLOURS["retail"], name="share of trades"))
style(fig, f"Slippage tolerance retail users gave ({len(bound):,} single-leg orders)", "Tolerance relative to Tessera's quote", "Share of trades", legend=False)
fig.update_yaxes(tickformat=".0%")
fig.show()

fig = go.Figure()
fig.add_bar(x=tol.index.astype(str), y=tol["degradation_p50"], name="median degradation", marker_color=COLOURS["fill"])
fig.add_scatter(x=tol.index.astype(str), y=tol["degradation_p90"], mode="markers", name="p90 degradation", marker=dict(color=COLOURS["quote"], size=11, symbol="diamond"))
style(fig, "Degradation taken vs tolerance given", "Tolerance the user allowed", "bps, positive = user got less than quoted").show()

at_bound = (bound["user_headroom_bps"] < 1).mean()
print(f"Median tolerance {bound['user_slippage_bps'].median():.0f} bps, median degradation {bound['quote_to_fill_bps'].median():.1f} bps, "
      f"median share of tolerance consumed {bound['user_slippage_consumed_fraction'].clip(-0.5, 1.5).median():.1%}, fills within 1 bps of the user's bound: {at_bound:.2%}")
display(tol.style.format({"n": "{:,}", "degradation_p50": "{:.2f}", "degradation_p90": "{:.2f}", "consumed_p50": "{:.1%}", "share_of_trades": "{:.0%}"}))
""")

md("""
## Degradation by trade size

- Is the skim a flat fee-like amount or does it scale with size like adverse selection would?
- Median and 90th percentile quote-to-fill bps by USD notional bucket, retail flow on pairs with a USDC leg
- The buckets are not like-for-like: KyberSwap and 0x (the low-degradation aggregators) route most of the $1k–100k flow, which pulls the middle buckets down; the per-aggregator chart above is the cleaner comparison
""")

code("""
bins = [0, 100, 1_000, 10_000, 100_000, np.inf]
labels = ["< $100", "$100–1k", "$1k–10k", "$10k–100k", "> $100k"]
retail = df[(df["flow"] == "retail") & df["notional_usd"].notna()].copy()
retail["size_bucket"] = pd.cut(retail["notional_usd"], bins=bins, labels=labels)
size = retail.groupby("size_bucket", observed=True)["quote_to_fill_bps"].agg(n="size", p50="median", p90=lambda s: s.quantile(0.9), mean="mean")
size["notional_usd"] = retail.groupby("size_bucket", observed=True)["notional_usd"].sum()
size["extracted_usd"] = retail.groupby("size_bucket", observed=True)["extracted_usd"].sum()

fig = go.Figure()
fig.add_bar(x=size.index.astype(str), y=size["p50"], name="median", marker_color=COLOURS["retail"])
fig.add_scatter(x=size.index.astype(str), y=size["p90"], mode="markers", name="p90", marker=dict(color=COLOURS["fill"], size=11, symbol="diamond"))
style(fig, "Retail quote-to-fill degradation by trade size", "Trade size, USD", "bps, positive = user got less than quoted").show()
display(size.style.format({"n": "{:,}", "p50": "{:.2f}", "p90": "{:.2f}", "mean": "{:.2f}", "notional_usd": "${:,.0f}", "extracted_usd": "${:,.0f}"}))
""")

md("""
# Question 3: how much?

## Against a fair price: was routing to Tessera worth it?

- Aggregators route to Tessera because its quote beats the AMM pools. We check that claim against the marginal price of the deepest pool for each pair at the same moment (end of the previous block)
- `quote vs fair` is the price improvement the aggregator saw; `fill vs fair` is what the user actually got
- Only WETH/USDC and cbBTC/USDC have a reference that is both deep and fresh (reference age ≤ 3 blocks); thin pairs are excluded because their pool price is stale and wide
""")

code("""
majors = df[df["pair"].isin(["WETH/USDC", "USDC/WETH", "cbBTC/USDC", "USDC/cbBTC"]) & (df["benchmark_age_blocks"] <= 3)]
fair = majors.groupby("flow").agg(
    n=("tx_hash", "size"),
    quote_vs_fair_p50=("quote_vs_benchmark_bps", "median"),
    fill_vs_fair_p50=("fill_vs_benchmark_bps", "median"),
    fill_vs_fair_mean=("fill_vs_benchmark_bps", "mean"),
    pool_fee_bps=("benchmark_fee_bps", "median"),
).reindex(FLOW_ORDER)

fig = go.Figure()
fig.add_bar(name="Tessera quote vs fair (what the aggregator saw)", x=FLOW_ORDER, y=fair["quote_vs_fair_p50"], marker_color=COLOURS["quote"])
fig.add_bar(name="Fill vs fair (what the user got)", x=FLOW_ORDER, y=-fair["fill_vs_fair_p50"], marker_color=COLOURS["fill"])
fig.add_hline(y=0, line=dict(color="#b0b0ad"))
fig.update_layout(barmode="group")
style(fig, "WETH/USDC and cbBTC/USDC: quote and fill vs the reference pool mid (median)", "Flow type", "bps better than fair (negative = worse)").show()
display(fair.style.format({"n": "{:,}", "quote_vs_fair_p50": "{:.2f}", "fill_vs_fair_p50": "{:.2f}", "fill_vs_fair_mean": "{:.2f}", "pool_fee_bps": "{:.1f}"}))
print("fill_vs_fair is positive when the user received less than the reference mid implied. A direct pool fill would have cost roughly the pool fee plus impact.")
""")

md("""
## Fill vs fair price, distribution

- The full distribution for the majors: bots sit slightly better than the fair mid, retail sits several basis points below it
- The gap between the two curves is the value transferred from retail users, in the same blocks, at the same venue
""")

code("""
fig = go.Figure()
for flow in FLOW_ORDER:
    s = majors.loc[majors["flow"] == flow, "fill_vs_benchmark_bps"].dropna().clip(-15, 25).sort_values()
    fig.add_scatter(x=s.values, y=np.linspace(0, 1, len(s)), mode="lines", name=f"{flow} (median {s.median():.1f} bps)", line=dict(color=COLOURS[flow], width=2))
fig.add_vline(x=0, line=dict(color="#b0b0ad", dash="dot"))
style(fig, "Fill vs reference pool mid, WETH/USDC and cbBTC/USDC", "Fill worse than fair price, bps", "Share of trades")
fig.update_yaxes(tickformat=".0%")
fig.show()
""")

md("""
## Dollars extracted from retail

- Quote-to-fill gap in USD, summed per day and split by aggregator, for retail trades with a USDC leg
- Negative days would mean users got more than quoted; there are none
""")

code("""
retail = df[(df["flow"] == "retail") & df["extracted_usd"].notna()]
daily = retail.pivot_table(index="day", columns="aggregator", values="extracted_usd", aggfunc="sum", fill_value=0)
top_aggs = daily.sum().sort_values(ascending=False).index[:7]
daily["other"] = daily.drop(columns=top_aggs).sum(axis=1)
daily = daily[list(top_aggs) + ["other"]]

fig = go.Figure()
for name in daily.columns:
    fig.add_bar(name=name, x=daily.index, y=daily[name], marker_color=AGGREGATOR_COLOURS.get(name, "#8a8a8a"))
fig.update_layout(barmode="stack")
style(fig, f"Daily USD extracted from retail via quote-to-fill gap (total ${retail['extracted_usd'].sum():,.0f})", "", "USD per day").show()

total_by_agg = retail.groupby("aggregator").agg(trades=("tx_hash", "size"), notional_usd=("notional_usd", "sum"), extracted_usd=("extracted_usd", "sum"))
total_by_agg["extracted_bps_of_volume"] = total_by_agg["extracted_usd"] / total_by_agg["notional_usd"] * 1e4
display(total_by_agg.sort_values("extracted_usd", ascending=False).style.format({"trades": "{:,}", "notional_usd": "${:,.0f}", "extracted_usd": "${:,.0f}", "extracted_bps_of_volume": "{:.2f}"}))
""")

md("""
## Extrapolation over the full history

- The quote enrichment covers the analysis window; raw trade volume is available for the whole ten months
- We apply the window's measured extraction per dollar of volume for each pair to the monthly full-history volume of that pair
- This is an estimate: it assumes the window's average skim rate and retail share of volume held over time. The time series above shows the skim comes in regimes (on in early July, off from late July to mid August, on again since), so any single month can be well above or below this average
""")

code("""
monthly = con.execute(\"\"\"
    SELECT date_trunc('month', t.timestamp) AS month, t.symbol_in || '/' || t.symbol_out AS pair,
           sum(CASE WHEN t.token_in = ? THEN t.amount_in_decimal WHEN t.token_out = ? THEN t.amount_out_decimal END) AS volume_usd,
           count(*) AS trades
    FROM trade_analysis t
    WHERE t.venue = 'tessera'
    GROUP BY 1, 2
\"\"\", [USDC, USDC]).df().dropna(subset=["volume_usd"])

# Measured extraction per dollar of total volume (all flow types) per pair in the window, so the window months reproduce the measured total
window_pairs = df[df["notional_usd"].notna()].groupby("pair").agg(volume_usd=("notional_usd", "sum"), extracted_usd=("extracted_usd", lambda s: s[df.loc[s.index, "flow"] == "retail"].sum()))
window_pairs["extraction_rate"] = window_pairs["extracted_usd"] / window_pairs["volume_usd"]
monthly = monthly.join(window_pairs["extraction_rate"], on="pair").dropna()
monthly["estimated_extracted_usd"] = monthly["volume_usd"] * monthly["extraction_rate"]
est = monthly.groupby("month").agg(volume_usd=("volume_usd", "sum"), estimated_extracted_usd=("estimated_extracted_usd", "sum"), trades=("trades", "sum"))
est = est[est["trades"] >= 1000]

fig = go.Figure(go.Bar(x=est.index, y=est["estimated_extracted_usd"], marker_color=COLOURS["retail"], name="estimated USD extracted"))
style(fig, f"Estimated USD extracted from retail per month (total ${est['estimated_extracted_usd'].sum():,.0f})", "", "USD", legend=False).show()
display(est.style.format({"volume_usd": "${:,.0f}", "estimated_extracted_usd": "${:,.0f}", "trades": "{:,}"}))
""")

md("""
# Why are retail fills worse?

## Who trades when: fills and price updates inside the block

- Overlay where in the block the keeper updates prices, where bots fill, and where retail fills
- Bots trade at the very top of the block (ahead of the first keeper update) or at the very bottom (after the refresh); almost never in between
- Retail transactions arrive through aggregator routers and ordinary mempool ordering and land in the middle of the block, after the top-of-block update
""")

code("""
edges = np.linspace(0, 1, 21)
centers = (edges[:-1] + edges[1:]) / 2
upd = con.execute(\"\"\"
    SELECT rel_gas_position FROM price_update_analysis
    WHERE selector = '0x1667d875' AND block_number >= ? AND rel_gas_position IS NOT NULL
\"\"\", [WINDOW_START_BLOCK]).df()["rel_gas_position"]

fig = go.Figure()
series = {"price update": upd, "retail": df.loc[df["flow"] == "retail", "rel_gas_position"], "bot": df.loc[df["flow"] == "bot", "rel_gas_position"]}
for name, s in series.items():
    hist, _ = np.histogram(s.dropna().clip(0, 0.999), bins=edges)
    fig.add_scatter(x=centers, y=hist / hist.sum(), mode="lines+markers", name=f"{name} (n={len(s):,})", line=dict(color=COLOURS[name], width=2), marker=dict(size=7))
style(fig, "Position in block: keeper price updates, bot fills and retail fills", "Position in block (0 = top, 1 = bottom)", "Share of transactions")
fig.update_yaxes(tickformat=".0%")
fig.show()
""")

md("""
## Degradation by position in block

- If the top-of-block reprice is the mechanism, fills that land after it should be worse than fills that land before it or after the end-of-block refresh
- Median quote-to-fill by in-block position, retail vs bot
- The venue does not discriminate by counterparty: bots that do land mid-block pay the same five bps as retail. The price is worse for everyone in the middle of every block; bots simply avoid the middle and retail cannot
""")

code("""
df["position_bin"] = pd.cut(df["rel_gas_position"], bins=np.linspace(0, 1, 11), labels=[f"{i/10:.1f}–{(i+1)/10:.1f}" for i in range(10)], include_lowest=True)
pos = df.groupby(["position_bin", "flow"], observed=True)["quote_to_fill_bps"].agg(n="size", p50="median").reset_index()

fig = go.Figure()
for flow in ["retail", "bot"]:
    sub = pos[(pos["flow"] == flow) & (pos["n"] >= 200)]
    fig.add_scatter(x=sub["position_bin"].astype(str), y=sub["p50"], mode="lines+markers", name=flow, line=dict(color=COLOURS[flow], width=2), marker=dict(size=8))
fig.add_hline(y=0, line=dict(color="#b0b0ad", dash="dot"))
style(fig, "Median quote-to-fill degradation by position in block", "Position in block (0 = top, 1 = bottom)", "bps, positive = user got less than quoted").show()
display(pos.pivot(index="position_bin", columns="flow", values="p50").style.format("{:.2f}"))
""")

md("""
## Did a price update land before the fill in the same block?

- For every trade, count the keeper price updates earlier in the same block
- Trades that settle after a reprice in their own block should be worse than trades that settle before any update
- The pattern is exact: zero updates before the fill gives zero degradation, one update (the top-of-block reprice) gives the full skim, two updates (the refresh has landed) gives zero again
""")

code("""
before = con.execute(\"\"\"
    SELECT t.tx_hash, t.log_index,
           count(u.tx_index) FILTER (WHERE u.tx_index < t.tx_index) AS updates_before,
           count(u.tx_index) FILTER (WHERE u.tx_index > t.tx_index) AS updates_after
    FROM trades t
    LEFT JOIN price_updates u ON u.block_number = t.block_number AND u.selector = '0x1667d875'
    WHERE t.venue = 'tessera' AND t.block_number >= ?
    GROUP BY 1, 2
\"\"\", [WINDOW_START_BLOCK]).df()
df = df.drop(columns=[c for c in ["updates_before", "updates_after"] if c in df.columns]).merge(before, on=["tx_hash", "log_index"], how="left")
df["updates_before_bucket"] = pd.cut(df["updates_before"], bins=[-1, 0, 1, 2, np.inf], labels=["0", "1", "2", "3+"])

ub = df.groupby(["updates_before_bucket", "flow"], observed=True)["quote_to_fill_bps"].agg(n="size", p50="median").reset_index()
fig = go.Figure()
for flow in ["retail", "bot"]:
    sub = ub[(ub["flow"] == flow) & (ub["n"] >= 200)]
    fig.add_bar(x=sub["updates_before_bucket"].astype(str), y=sub["p50"], name=flow, marker_color=COLOURS[flow])
fig.update_layout(barmode="group")
style(fig, "Median quote-to-fill degradation by number of price updates earlier in the same block", "Keeper price updates before the fill, same block", "bps, positive = user got less than quoted").show()
display(ub.pivot(index="updates_before_bucket", columns="flow", values=["n", "p50"]).style.format("{:,.2f}"))
""")

md("""
## Was the worse price permanent or temporary?

- Compare each fill with the quote at the end of the previous block (before) and the quote at the end of its own block (after)
- If the fill is worse than *both*, the price was widened around the fill and tightened again afterwards: the temporary widening 0x described
- If the fill is only worse than the earlier quote, the market moved and the new price stayed
- Roughly three quarters of retail fills are worse than both quotes: the worse price is a transient state of the venue, not a market move
""")

code("""
retail = df[df["flow"] == "retail"].dropna(subset=["quote_to_fill_bps", "same_block_quote_to_fill_bps"])
worse_than_both = ((retail["quote_to_fill_bps"] > 1) & (retail["same_block_quote_to_fill_bps"] > 1)).mean()
worse_only_before = ((retail["quote_to_fill_bps"] > 1) & (retail["same_block_quote_to_fill_bps"] <= 1)).mean()

comp = pd.DataFrame({
    "vs quote before (end of previous block)": [df.loc[df["flow"] == f, "quote_to_fill_bps"].median() for f in ["retail", "bot"]],
    "vs quote after (end of own block)": [df.loc[df["flow"] == f, "same_block_quote_to_fill_bps"].median() for f in ["retail", "bot"]],
}, index=["retail", "bot"])

fig = go.Figure()
fig.add_bar(name="vs quote before the fill", x=comp.index, y=comp.iloc[:, 0], marker_color=COLOURS["quote"])
fig.add_bar(name="vs quote after the fill", x=comp.index, y=comp.iloc[:, 1], marker_color=COLOURS["fill"])
fig.update_layout(barmode="group")
fig.add_hline(y=0, line=dict(color="#b0b0ad"))
style(fig, "Median fill degradation against the quote before and after the fill", "Flow type", "bps, positive = fill worse than quote").show()
print(f"Retail fills worse than both the earlier and the later quote by more than 1 bps: {worse_than_both:.0%}; worse only than the earlier quote: {worse_only_before:.0%}")
""")

md("""
## What changes in the price update payload?

- The keeper payload is three packed 32-bit words that we could not fully decode without the engine source
- The first word is, however, revealing: the top-of-block update sets it to a baseline plus an offset, the end-of-block update resets it to the baseline
- Whatever the parameter is, the venue is in one state while retail settles and in another when the aggregator quotes and bots trade
""")

code("""
words = con.execute(\"\"\"
    SELECT rel_gas_position, ('0x' || substr(input, 51, 8))::UBIGINT AS word1
    FROM price_update_analysis
    WHERE selector = '0x1667d875' AND block_number >= ? AND length(input) >= 74 AND rel_gas_position IS NOT NULL
\"\"\", [WINDOW_START_BLOCK]).df()
baseline = words["word1"].mode().iloc[0]
words["offset"] = words["word1"].astype("int64") - int(baseline)
words["position_bin"] = pd.cut(words["rel_gas_position"], bins=np.linspace(0, 1, 11), labels=[f"{i/10:.1f}–{(i+1)/10:.1f}" for i in range(10)], include_lowest=True)
w = words.groupby("position_bin", observed=True)["offset"].agg(n="size", share_nonzero=lambda s: (s != 0).mean(), median_abs_offset=lambda s: s.abs().median())

fig = go.Figure(go.Bar(x=w.index.astype(str), y=w["share_nonzero"], marker_color=COLOURS["price update"], name="share of updates with offset ≠ 0"))
style(fig, f"Share of price updates whose first payload word deviates from the baseline ({baseline})", "Position in block (0 = top, 1 = bottom)", "Share of updates", legend=False)
fig.update_yaxes(tickformat=".0%")
fig.show()
display(w.style.format({"n": "{:,}", "share_nonzero": "{:.1%}", "median_abs_offset": "{:,.0f}"}))
""")

md("""
# Question 4: when, and why?

## Regimes in retail fill quality

- Is the skim constant? We detect change points in the daily median of retail fills vs the reference pool mid (majors, fresh reference, days with at least 200 retail trades) using binary segmentation with a minimum segment of five days and a minimum median shift of two bps
- Regimes with a median shortfall of three bps or more are labelled *skim*, the rest *honest*
- A regime detected on the outcome says nothing about its cause on its own; the composition table shows what else changed between regimes, and the next section tests the keeper independently
- Days with too few trades are excluded from the fit rather than filled in, and the change points are re-run at 100 and 400 trades/day to show how sensitive the boundaries are
""")

code("""
fresh = df[df["pair"].isin(MAJORS) & (df["benchmark_age_blocks"] <= 3)].copy()
retail_fresh = fresh[fresh["flow"] == "retail"]
daily_counts = retail_fresh.groupby("day")["fill_vs_benchmark_bps"].size()
daily_median = retail_fresh.groupby("day")["fill_vs_benchmark_bps"].median()

MIN_TRADES_PER_DAY = 200
usable = daily_median[daily_counts >= MIN_TRADES_PER_DAY]
cuts = detect_change_points(usable, min_size=5, min_shift=2.0)
regimes = build_regimes(usable, cuts)
df["regime"] = assign_regime(df["timestamp"], regimes)
fresh["regime"] = assign_regime(fresh["timestamp"], regimes)

print(f"{len(daily_median)} days in window, {len(usable)} with >= {MIN_TRADES_PER_DAY} retail trades on the majors, {len(cuts)} change points: {[c.date().isoformat() for c in cuts]}")

sensitivity = []
for threshold in (100, 200, 400):
    u = daily_median[daily_counts >= threshold]
    sensitivity.append({"min trades/day": threshold, "days used": len(u), "change points": ", ".join(c.date().isoformat() for c in detect_change_points(u, 5, 2.0))})
display(pd.DataFrame(sensitivity).style.hide(axis="index"))

fig = go.Figure()
fig.add_scatter(x=daily_median.index, y=-daily_median, mode="lines+markers", name="retail fill vs fair, daily median", line=dict(color=COLOURS["retail"], width=2), marker=dict(size=6))
thin = daily_median[daily_counts < MIN_TRADES_PER_DAY]
fig.add_scatter(x=thin.index, y=-thin, mode="markers", name=f"days below {MIN_TRADES_PER_DAY} trades (not used)", marker=dict(color=COLOURS["unlabelled"], size=9, symbol="x"))
fig.add_hline(y=0, line=dict(color="#b0b0ad", dash="dot"))
add_regime_bands(fig, regimes)
style(fig, "Retail fill vs reference pool mid, daily median, with detected regimes", "", "bps better than fair (negative = worse)")
fig.update_yaxes(range=[-12, 3])
fig.show()

comp = fresh.groupby("regime").agg(
    start=("timestamp", "min"), end=("timestamp", "max"), trades=("tx_hash", "size"),
    retail_share=("flow", lambda s: (s == "retail").mean()), bot_share=("flow", lambda s: (s == "bot").mean()),
    weth_share=("pair", lambda s: s.isin(["WETH/USDC", "USDC/WETH"]).mean()),
    median_trade_usd=("notional_usd", "median"),
    retail_fill_vs_fair_p50=("fill_vs_benchmark_bps", lambda s: s[fresh.loc[s.index, "flow"] == "retail"].median()),
    quote_vs_fair_p50=("quote_vs_benchmark_bps", "median"),
)
comp["label"] = regimes.set_index("regime_id")["label"]
top_aggs = df[df["flow"] == "retail"]["aggregator"].value_counts().index[:4]
for a in top_aggs:
    comp[f"{a} share of retail"] = fresh[fresh["flow"] == "retail"].groupby("regime")["aggregator"].apply(lambda s: (s == a).mean())
display(comp.style.format({"start": "{:%Y-%m-%d}", "end": "{:%Y-%m-%d}", "trades": "{:,}", "retail_share": "{:.0%}", "bot_share": "{:.0%}", "weth_share": "{:.0%}", "median_trade_usd": "${:,.0f}", "retail_fill_vs_fair_p50": "{:.2f}", "quote_vs_fair_p50": "{:.2f}", **{f"{a} share of retail": "{:.0%}" for a in top_aggs}}))
""")

md("""
## Does the keeper switch with the regimes?

- The regimes above were found on retail outcomes, so keeper behaviour inside them can only co-vary by construction. The independent test: run the same change-point detector on two keeper series that never look at fill quality — the daily share of top-of-block updates carrying the payload offset, and updates per block — and compare the boundary dates
- Boundaries that coincide within a day across independently detected series mean the keeper's state and the fill-quality regime switch together; boundaries that do not coincide are reported as such
- Two updaters write to the price store: the main keeper (selector `0x1667d875`, one wallet, about two updates per block) and a secondary fleet of rotating wallets and selectors. Days on which the main keeper is silent are shown explicitly
- Result: the main keeper's cadence has change points on exactly the same days as the fill-quality regimes. It was silent from 22 July to 27 July — the honest spell — while the secondary fleet kept running; keeper-update patterns switch on the same days as the fill-quality regimes. The payload offset share does not change (it is near 100 % whenever the main keeper is active), so the offset is a property of the main keeper's updates rather than a dial that is turned
""")

code("""
keeper_daily = con.execute(\"\"\"
    WITH days AS (SELECT date_trunc('day', timestamp) AS day, count(*) AS blocks FROM blocks WHERE block_number >= ? GROUP BY 1),
    u AS (
        SELECT date_trunc('day', timestamp) AS day, rel_gas_position, ('0x' || substr(input, 51, 8))::UBIGINT AS word1
        FROM price_update_analysis
        WHERE selector = '0x1667d875' AND block_number >= ? AND length(input) >= 74 AND rel_gas_position IS NOT NULL
    ),
    m AS (SELECT day, mode(word1) AS baseline FROM u GROUP BY 1),
    agg AS (
        SELECT u.day, count(*) AS updates, any_value(m.baseline) AS baseline,
               avg(CASE WHEN u.rel_gas_position < 0.1 THEN (u.word1 <> m.baseline)::INT END) AS top_offset_share
        FROM u JOIN m ON m.day = u.day GROUP BY u.day
    ),
    other AS (
        SELECT date_trunc('day', timestamp) AS day, count(*) AS other_updates
        FROM price_update_analysis WHERE selector <> '0x1667d875' AND block_number >= ? GROUP BY 1
    )
    SELECT days.day, coalesce(agg.updates, 0) AS updates, coalesce(agg.updates, 0) * 1.0 / days.blocks AS updates_per_block,
           coalesce(other.other_updates, 0) * 1.0 / days.blocks AS other_updates_per_block,
           agg.baseline, agg.top_offset_share
    FROM days LEFT JOIN agg ON agg.day = days.day LEFT JOIN other ON other.day = days.day
    ORDER BY days.day
\"\"\", [WINDOW_START_BLOCK, WINDOW_START_BLOCK, WINDOW_START_BLOCK]).df()
keeper_daily["day"] = pd.to_datetime(keeper_daily["day"])
keeper_daily = keeper_daily.set_index("day")
# Drop partial first and last days so cadence is per full day
keeper_daily = keeper_daily.iloc[1:-1]

offset_cuts = detect_change_points(keeper_daily["top_offset_share"].dropna() * 100, min_size=5, min_shift=10.0)
cadence_cuts = detect_change_points(keeper_daily["updates_per_block"], min_size=3, min_shift=0.5)

boundaries = pd.DataFrame({"fill-quality regime boundary": [c.date() for c in cuts]})
def nearest(target, candidates):
    if not candidates:
        return None, None
    d = min(candidates, key=lambda c: abs((c - target).days))
    return d.date(), (d - target).days
boundaries["nearest keeper offset boundary"], boundaries["offset Δ days"] = zip(*[nearest(c, offset_cuts) for c in cuts]) if len(cuts) else ([], [])
boundaries["nearest keeper cadence boundary"], boundaries["cadence Δ days"] = zip(*[nearest(c, cadence_cuts) for c in cuts]) if len(cuts) else ([], [])
print("Keeper offset-share change points:", [c.date().isoformat() for c in offset_cuts] or "none")
print("Keeper cadence change points:", [c.date().isoformat() for c in cadence_cuts] or "none")
silent = keeper_daily[keeper_daily["updates"] == 0].index
print("Days with no main-keeper updates at all:", [d.date().isoformat() for d in silent] or "none")
display(boundaries.style.hide(axis="index").format(na_rep="—"))

fig = go.Figure()
fig.add_scatter(x=keeper_daily.index, y=keeper_daily["top_offset_share"], mode="lines+markers", name="share of top-of-block updates with payload offset", line=dict(color=COLOURS["price update"], width=2), marker=dict(size=6))
for c in offset_cuts:
    fig.add_vline(x=c, line=dict(color=COLOURS["price update"], dash="dash", width=1.5))
add_regime_bands(fig, regimes)
style(fig, "Keeper payload offset share (top of block), daily, with its own change points (dashed) and the fill-quality regimes (bands)", "", "Share of top-of-block updates")
fig.update_yaxes(tickformat=".0%")
fig.show()

fig = go.Figure()
fig.add_scatter(x=keeper_daily.index, y=keeper_daily["updates_per_block"], mode="lines+markers", name="main keeper updates per block", line=dict(color=COLOURS["price update"], width=2), marker=dict(size=6))
fig.add_scatter(x=keeper_daily.index, y=keeper_daily["other_updates_per_block"], mode="lines+markers", name="secondary updater fleet updates per block", line=dict(color=COLOURS["unlabelled"], width=1.5), marker=dict(size=5))
for c in cadence_cuts:
    fig.add_vline(x=c, line=dict(color=COLOURS["price update"], dash="dash", width=1.5))
add_regime_bands(fig, regimes)
style(fig, "Keeper update cadence, daily, with its own change points (dashed) and the fill-quality regimes (bands)", "", "Updates per block").show()

keeper_daily["regime"] = assign_regime(pd.Series(keeper_daily.index, index=keeper_daily.index), regimes)
display(keeper_daily.groupby("regime").agg(days=("updates", "size"), main_keeper_updates_per_block=("updates_per_block", "median"), secondary_updates_per_block=("other_updates_per_block", "median"), top_offset_share=("top_offset_share", "median"), baseline_values=("baseline", "nunique")).join(regimes.set_index("regime_id")[["label"]]).style.format({"main_keeper_updates_per_block": "{:.2f}", "secondary_updates_per_block": "{:.2f}", "top_offset_share": "{:.1%}"}, na_rep="—"))
""")

md("""
## Degradation after a price update, controlling for position in block

- The number of keeper updates before a fill is largely a function of where in the block the fill lands, and late-block execution, gas competition, size and aggregator mix can all move fill quality on their own
- So the comparison is made *within* narrow position bins: at the same position, is a retail fill with exactly one prior update worse than one with none, or with two (refresh landed)?
- Cells with fewer than 200 trades are suppressed; shown per regime. Bars for zero prior updates sit at zero bps and are therefore invisible: at the same position in the block, a fill with no keeper update before it is filled at the quote
""")

code("""
retail = df[(df["flow"] == "retail") & df["updates_before"].notna()].copy()
retail["position_bin"] = pd.cut(retail["rel_gas_position"], bins=np.linspace(0, 1, 11), labels=[f"{i/10:.1f}–{(i+1)/10:.1f}" for i in range(10)], include_lowest=True)
retail["updates_before_bucket"] = pd.cut(retail["updates_before"], bins=[-1, 0, 1, np.inf], labels=["0", "1", "2+"])
cells = retail.groupby(["regime", "position_bin", "updates_before_bucket"], observed=True)["quote_to_fill_bps"].agg(n="size", p50="median").reset_index()
cells = cells[cells["n"] >= 200]
cells["regime_label"] = cells["regime"].map(regimes.set_index("regime_id")["label"])

fig = make_subplots(rows=1, cols=len(regimes), shared_yaxes=True, subplot_titles=[f"regime {int(r.regime_id)}: {r.label} ({r.start:%d %b}–{r.end:%d %b})" for r in regimes.itertuples()])
bucket_colours = {"0": COLOURS["quote"], "1": COLOURS["fill"], "2+": COLOURS["price update"]}
shown = set()
for col, r in enumerate(regimes.itertuples(), start=1):
    sub = cells[cells["regime"] == r.regime_id]
    for bucket, colour in bucket_colours.items():
        b = sub[sub["updates_before_bucket"] == bucket]
        if b.empty:
            continue
        fig.add_bar(x=b["position_bin"].astype(str), y=b["p50"], name=f"{bucket} prior updates", marker_color=colour, showlegend=bucket not in shown, row=1, col=col)
        shown.add(bucket)
fig.update_layout(barmode="group")
style(fig, "Retail median quote-to-fill by position in block and prior keeper updates, per regime (cells with ≥ 200 trades)", "Position in block", "bps, positive = user got less than quoted")
fig.update_layout(height=650)
fig.show()

pivot = cells.pivot_table(index=["regime_label", "position_bin"], columns="updates_before_bucket", values="p50", observed=True)
display(pivot.style.format("{:.2f}", na_rep="—"))
""")

md("""
## Markouts: what does the market do after a fill?

- For every fill on the majors we compare the fill price with the reference pool mid 5, 30 and 150 blocks later (10 s, 1 min, 5 min). The baseline is the trade's own fill price, so there is no look-ahead; the horizon price must come from a swap no older than half the horizon or the observation is dropped
- Two measures, both signed so that positive means the price subsequently moved in the trader's favour: the *markout* compares the horizon mid with the fill price (so it contains the fill shortfall), the *drift* compares the horizon mid with the mid before the trade (the information content of the flow, free of the fill)
- The question this answers: is the flow being charged the skim the kind of flow that predicts price moves?
- Result: the mid does not drift after either flow (both within about half a bp at every horizon, bots slightly negative). Retail's negative markout is the fill shortfall itself and does not grow with the horizon. Under this definition, neither retail nor bot fills are followed by favourable price moves, so an adverse-selection justification for the spread is not visible in the flow being charged
""")

code("""
HORIZONS = [5, 30, 150]
base = con.execute(\"\"\"
    SELECT tx_hash, log_index, block_number, pool, sells_token0, fill_price_t1_per_t0, benchmark_price AS pre_trade_mid
    FROM trade_vs_benchmark
    WHERE venue = 'tessera' AND block_number >= ? AND benchmark_age_blocks <= 3
      AND symbol_in || '/' || symbol_out IN ('WETH/USDC', 'USDC/WETH', 'cbBTC/USDC', 'USDC/cbBTC')
\"\"\", [WINDOW_START_BLOCK]).df()
prices = con.execute(\"\"\"
    SELECT pool, block_number AS price_block, price AS horizon_price FROM benchmark_block_prices
    WHERE block_number >= ? AND pool IN (SELECT DISTINCT pool FROM trade_benchmarks) ORDER BY price_block
\"\"\", [WINDOW_START_BLOCK - 1000]).df()

# DuckDB 1.5 hangs when materialising an ASOF join with a full projection, so the "last price at or before the horizon" lookup is done in pandas
markout_frames = []
for h in HORIZONS:
    left = base.assign(target=base["block_number"] + h).sort_values("target")
    m = pd.merge_asof(left, prices, left_on="target", right_on="price_block", by="pool", direction="backward")
    m["age"] = m["target"] - m["price_block"]
    m = m[m["age"] <= h / 2]
    direction = np.where(m["sells_token0"], -1.0, 1.0)
    m["markout_bps"] = direction * (m["horizon_price"] - m["fill_price_t1_per_t0"]) / m["fill_price_t1_per_t0"] * 1e4
    # Drift of the mid itself from before the trade to the horizon: the information content of the flow, free of the fill shortfall
    m["drift_bps"] = direction * (m["horizon_price"] - m["pre_trade_mid"]) / m["pre_trade_mid"] * 1e4
    m["horizon"] = h
    markout_frames.append(m[["tx_hash", "log_index", "horizon", "markout_bps", "drift_bps"]])
markouts = pd.concat(markout_frames).merge(df[["tx_hash", "log_index", "flow", "regime"]], on=["tx_hash", "log_index"], how="left")

by_h = markouts.groupby(["horizon", "flow"]).agg(n=("markout_bps", "size"), markout_median=("markout_bps", "median"), markout_mean=("markout_bps", "mean"), drift_median=("drift_bps", "median"), drift_mean=("drift_bps", "mean")).reset_index()
fig = make_subplots(rows=1, cols=2, shared_yaxes=True, subplot_titles=["Markout vs the fill price", "Drift of the mid from before the trade"])
for flow in ["retail", "bot"]:
    sub = by_h[by_h["flow"] == flow]
    fig.add_bar(x=[f"{h} blocks" for h in sub["horizon"]], y=sub["markout_median"], name=f"{flow}", marker_color=COLOURS[flow], row=1, col=1)
    fig.add_bar(x=[f"{h} blocks" for h in sub["horizon"]], y=sub["drift_median"], name=f"{flow}", marker_color=COLOURS[flow], showlegend=False, row=1, col=2)
fig.update_layout(barmode="group")
fig.add_hline(y=0, line=dict(color="#b0b0ad"))
style(fig, "Median price move after the fill, retail vs bot (majors)", "Horizon after the fill", "bps, positive = in the trader's favour").show()
display(by_h.set_index(["horizon", "flow"]).style.format({"n": "{:,.0f}", "markout_median": "{:.2f}", "markout_mean": "{:.2f}", "drift_median": "{:.2f}", "drift_mean": "{:.2f}"}))

fig = go.Figure()
for flow in FLOW_ORDER:
    s = markouts.loc[(markouts["horizon"] == 30) & (markouts["flow"] == flow), "markout_bps"].dropna().clip(-30, 30).sort_values()
    if len(s):
        fig.add_scatter(x=s.values, y=np.linspace(0, 1, len(s)), mode="lines", name=f"{flow} (median {s.median():.1f} bps, n={len(s):,})", line=dict(color=COLOURS[flow], width=2))
fig.add_vline(x=0, line=dict(color="#b0b0ad", dash="dot"))
style(fig, "Markout 30 blocks after the fill, cumulative distribution", "bps, positive = price moved in the trader's favour", "Share of trades")
fig.update_yaxes(tickformat=".0%")
fig.show()

by_regime = markouts[markouts["flow"] == "retail"].groupby(["regime", "horizon"])["markout_bps"].agg(n="size", median="median").reset_index()
by_regime_table = by_regime.pivot(index="regime", columns="horizon", values=["n", "median"])
by_regime_table.columns = [f"{stat} at {h} blocks" for stat, h in by_regime_table.columns]
display(by_regime_table.join(regimes.set_index("regime_id")[["label"]]).style.format({c: "{:,.0f}" if c.startswith("n ") else "{:.2f}" for c in by_regime_table.columns}))
""")

md("""
## Benchmark-relative shortfall and gain by flow type

- Per day: how much retail flow received less than the reference mid, and how much bot flow received more, in USD on the majors
- These are two attributions against a common benchmark, not two sides of a transfer: the trades are not matched, bot gains can come from timing and inventory unrelated to retail's shortfall, and the benchmark omits the pool's fee and impact. The two lines are not additive and nothing here is a venue P&L
- Whether bot gains rise when the skim is off is worth knowing either way: it is the observation a venue would cite to justify the skim
""")

code("""
fresh["shortfall_usd"] = fresh["fill_vs_benchmark_bps"] / 1e4 * fresh["notional_usd"]
ledger = fresh.pivot_table(index="day", columns="flow", values="shortfall_usd", aggfunc="sum").fillna(0)
ledger["retail shortfall"] = ledger.get("retail", 0)
ledger["bot gain"] = -ledger.get("bot", 0)

fig = go.Figure()
fig.add_scatter(x=ledger.index, y=ledger["retail shortfall"], mode="lines+markers", name="retail shortfall vs fair (USD/day)", line=dict(color=COLOURS["retail"], width=2), marker=dict(size=6))
fig.add_scatter(x=ledger.index, y=ledger["bot gain"], mode="lines+markers", name="bot gain vs fair (USD/day)", line=dict(color=COLOURS["bot"], width=2), marker=dict(size=6))
fig.add_hline(y=0, line=dict(color="#b0b0ad"))
add_regime_bands(fig, regimes)
style(fig, "Daily benchmark-relative shortfall (retail) and gain (bots), majors, signed", "", "USD per day").show()

fresh["regime_label"] = fresh["regime"].map(regimes.set_index("regime_id")["label"])
per_regime = fresh.groupby(["regime", "regime_label", "flow"]).agg(trades=("tx_hash", "size"), notional_usd=("notional_usd", "sum"), signed_shortfall_usd=("shortfall_usd", "sum"), gross_shortfall_usd=("shortfall_usd", lambda s: s[s > 0].sum())).reset_index()
per_regime["days"] = per_regime["regime"].map(regimes.set_index("regime_id")["days"])
per_regime["signed_shortfall_per_day"] = per_regime["signed_shortfall_usd"] / per_regime["days"]
display(per_regime[per_regime["flow"].isin(["retail", "bot"])].style.format({"trades": "{:,}", "notional_usd": "${:,.0f}", "signed_shortfall_usd": "${:,.0f}", "gross_shortfall_usd": "${:,.0f}", "signed_shortfall_per_day": "${:,.0f}"}).hide(axis="index"))
print(f"Retail shortfall vs fair on the majors: ${ledger['retail shortfall'].sum():,.0f}. "
      f"For comparison, retail shortfall vs Tessera's own quote on all pairs (earlier section): ${df.loc[df['flow'] == 'retail', 'extracted_usd'].sum():,.0f}. "
      "The two differ because the baselines differ (pool mid vs Tessera's end-of-previous-block quote) and the pair sets differ.")
""")

md("""
## Aggregators across regimes

- Tessera-only data cannot show routing decisions: less Tessera volume from an aggregator can mean less routing, less user demand, market-wide volume, a different pair mix or label coverage
- What it can show is each aggregator's observed Tessera activity and fill quality per regime, normalised by that aggregator's own window average, next to total Tessera volume as a market-wide control
- Two things stand out in the weekly view: 0x's Tessera flow on the majors collapses in early July and its residual fills move from about six bps below fair to near fair by mid-August, consistent with its public statement that it monitors and cuts propAMM sources; KyberSwap flow fills near fair throughout. Whether either reflects routing decisions or user demand cannot be told from Tessera's side alone
""")

code("""
retail_all = df[(df["flow"] == "retail") & df["notional_usd"].notna()].copy()
retail_all["week"] = retail_all["timestamp"].dt.to_period("W").dt.start_time
top_aggs = retail_all.groupby("aggregator")["notional_usd"].sum().sort_values(ascending=False).index[:7]
retail_all["aggregator_group"] = np.where(retail_all["aggregator"].isin(top_aggs), retail_all["aggregator"], "other")

weekly = retail_all.pivot_table(index="week", columns="aggregator_group", values="notional_usd", aggfunc="sum").fillna(0)
share = weekly.div(weekly.sum(axis=1), axis=0)
fig = go.Figure()
for name in list(top_aggs) + ["other"]:
    if name in share:
        fig.add_scatter(x=share.index, y=share[name], mode="lines+markers", name=name, line=dict(color=AGGREGATOR_COLOURS.get(name, "#8a8a8a"), width=2), marker=dict(size=6), stackgroup="one")
add_regime_bands(fig, regimes)
style(fig, "Share of retail Tessera volume by aggregator, weekly", "", "Share of retail volume")
fig.update_yaxes(tickformat=".0%")
fig.show()

fresh_retail = fresh[fresh["flow"] == "retail"].copy()
fresh_retail["week"] = fresh_retail["timestamp"].dt.to_period("W").dt.start_time
wq = fresh_retail[fresh_retail["aggregator"].isin(top_aggs)].groupby(["week", "aggregator"])["fill_vs_benchmark_bps"].agg(n="size", p50="median").reset_index()
wq = wq[wq["n"] >= 100]
fig = go.Figure()
for name in top_aggs:
    sub = wq[wq["aggregator"] == name]
    fig.add_scatter(x=sub["week"], y=-sub["p50"], mode="lines+markers", name=name, line=dict(color=AGGREGATOR_COLOURS.get(name, "#8a8a8a"), width=2), marker=dict(size=6))
fig.add_hline(y=0, line=dict(color="#b0b0ad", dash="dot"))
add_regime_bands(fig, regimes)
style(fig, "Retail fill vs fair by aggregator, weekly median (majors, weeks with ≥ 100 trades)", "", "bps better than fair (negative = worse)")
fig.update_yaxes(range=[-12, 3])
fig.show()

regime_days = regimes.set_index("regime_id")["days"]
vol = retail_all.groupby(["aggregator_group", "regime"])["notional_usd"].sum().unstack("regime").fillna(0)
vol_per_day = vol.div(regime_days, axis=1)
normalised = vol_per_day.div(vol_per_day.mean(axis=1), axis=0)
normalised.columns = [f"regime {int(c)} ({regimes.set_index('regime_id').loc[c, 'label']})" for c in normalised.columns]
total = retail_all.groupby("regime")["notional_usd"].sum() / regime_days
normalised.loc["all retail (control)"] = (total / total.mean()).values
fq_stats = fresh_retail.groupby(["aggregator", "regime"])["fill_vs_benchmark_bps"].agg(n="size", p50="median").reset_index()
fq_stats.loc[fq_stats["n"] < 100, "p50"] = np.nan
fq = fq_stats.pivot(index="aggregator", columns="regime", values="p50")
fq.columns = [f"fill vs fair, regime {int(c)}" for c in fq.columns]
display(normalised.style.format("{:.2f}×"))
display(fq.loc[fq.index.isin(top_aggs)].style.format("{:.2f}", na_rep="— (< 100 trades)"))
""")

md("""
## Damage at the recipient level

- From aggregate dollars to per-user impact, named precisely: *estimated Tessera-leg shortfall by recipient key*. The shortfall is against Tessera's reconstructed previous-block quote (not necessarily the price the user saw) for one leg of a possibly multi-hop route; the fair-value variant excludes the pool's fee and impact
- The recipient key is the router order's recipient when a trace decoded one (smart wallets and relayed orders), otherwise the transaction sender; key coverage is reported
- "Affected" means a positive net shortfall vs quote. Addresses are truncated; this is aggregate research
""")

code("""
recipients = con.execute(\"\"\"
    SELECT tx_hash, any_value(order_recipient) AS order_recipient FROM trade_orders WHERE order_recipient IS NOT NULL GROUP BY 1
\"\"\").df()
known_contracts = {a.lower() for (a,) in con.execute("SELECT address FROM router_labels").fetchall()} | {"0x0000000000000000000000000000000000000000"}
retail_w = df[df["flow"] == "retail"].merge(recipients, on="tx_hash", how="left")
# Routers use the zero address for "msg.sender", and some orders name a router or wrapper as recipient; neither is a user
retail_w.loc[retail_w["order_recipient"].str.lower().isin(known_contracts), "order_recipient"] = np.nan
retail_w["key"] = retail_w["order_recipient"].fillna(retail_w["tx_from"])
retail_w = retail_w[~retail_w["key"].str.lower().isin(known_contracts)]
retail_w["key_source"] = np.where(retail_w["order_recipient"].notna(), "order recipient", "transaction sender")
retail_w["fair_shortfall_usd"] = retail_w["fill_vs_benchmark_bps"] / 1e4 * retail_w["notional_usd"]

wallets = retail_w.groupby("key").agg(trades=("tx_hash", "size"), notional_usd=("notional_usd", "sum"), shortfall_vs_quote_usd=("extracted_usd", "sum"), shortfall_vs_fair_usd=("fair_shortfall_usd", "sum"), first_seen=("timestamp", "min"), last_seen=("timestamp", "max"))
affected = wallets[wallets["shortfall_vs_quote_usd"] > 0]
share_once = (wallets["trades"] == 1).mean()
print(f"{len(wallets):,} recipient keys ({retail_w['key_source'].value_counts(normalize=True).round(2).to_dict()}); "
      f"{len(affected):,} affected ({len(affected) / len(wallets):.0%}), {(wallets['shortfall_vs_quote_usd'] == 0).mean():.0%} net zero, {(wallets['shortfall_vs_quote_usd'] < 0).mean():.0%} net negative; "
      f"{share_once:.0%} of keys traded once. Median affected key: ${affected['shortfall_vs_quote_usd'].median():,.2f} over {affected['trades'].median():.0f} trades.")

sized = affected[affected["notional_usd"] >= 100]
print(f"Keys with at least $100 traded: {len(sized):,} ({len(sized) / len(affected):.0%} of affected keys); median shortfall ${sized['shortfall_vs_quote_usd'].median():,.2f} over {sized['trades'].median():.0f} trades; the rest are dust trades.")
fig = go.Figure()
for label, frame in (("all affected keys", affected), ("keys with ≥ $100 traded", sized)):
    v = frame["shortfall_vs_quote_usd"].sort_values()
    fig.add_scatter(x=v.values, y=np.linspace(0, 1, len(v)), mode="lines", name=f"{label} (median ${v.median():,.2f})", line=dict(color=COLOURS["retail"] if label.startswith("all") else COLOURS["fill"], width=2))
style(fig, "Estimated Tessera-leg shortfall per affected recipient key, cumulative distribution", "USD (log scale)", "Share of keys")
fig.update_xaxes(type="log", range=[-3, 4])
fig.update_yaxes(tickformat=".0%")
fig.show()

trades_hist = wallets["trades"].clip(upper=20).value_counts().sort_index()
fig = go.Figure(go.Bar(x=[str(i) if i < 20 else "20+" for i in trades_hist.index], y=trades_hist.values / trades_hist.sum(), marker_color=COLOURS["retail"]))
style(fig, "Tessera trades per recipient key", "Trades in window", "Share of keys", legend=False)
fig.update_yaxes(tickformat=".0%")
fig.show()

sorted_loss = affected["shortfall_vs_quote_usd"].sort_values(ascending=False)
cum = sorted_loss.cumsum() / sorted_loss.sum()
print(f"Top 1% of affected keys carry {cum.iloc[int(len(cum) * 0.01)]:.0%} of the shortfall, top 10% carry {cum.iloc[int(len(cum) * 0.10)]:.0%}.")
top_keys = affected.sort_values("shortfall_vs_quote_usd", ascending=False).head(20).copy()
top_keys.index = [k[:6] + "…" + k[-4:] for k in top_keys.index]
display(top_keys.style.format({"trades": "{:,}", "notional_usd": "${:,.0f}", "shortfall_vs_quote_usd": "${:,.0f}", "shortfall_vs_fair_usd": "${:,.0f}", "first_seen": "{:%Y-%m-%d}", "last_seen": "{:%Y-%m-%d}"}))
""")

md("""
## Summary

- Headline numbers for the analysis window
""")

code("""
retail = df[df["flow"] == "retail"]
bots = df[df["flow"] == "bot"]
majors_retail = majors[majors["flow"] == "retail"]
majors_bot = majors[majors["flow"] == "bot"]
best = agg["p50"].idxmin(); worst = agg["p50"].idxmax()
m30 = markouts[markouts["horizon"] == 30].groupby("flow")["markout_bps"].median()
d30 = markouts[markouts["horizon"] == 30].groupby("flow")["drift_bps"].median()
skim_rows = regimes[regimes["label"] == "skim"]; honest_rows = regimes[regimes["label"] == "honest"]
offset_by_regime = keeper_daily.groupby("regime")["top_offset_share"].median()
retail_ledger = per_regime[per_regime["flow"] == "retail"].set_index("regime")["signed_shortfall_usd"]
bot_ledger = per_regime[per_regime["flow"] == "bot"].set_index("regime")["signed_shortfall_usd"]

headline = pd.DataFrame([
    ("Analysis window", f"{df['timestamp'].min():%Y-%m-%d} – {df['timestamp'].max():%Y-%m-%d}"),
    ("Tessera trades in window", f"{len(df):,} (retail {len(retail):,}, bot {len(bots):,}, unlabelled {(df['flow'] == 'unlabelled').sum():,})"),
    ("Retail volume with USDC leg", f"${retail['notional_usd'].sum():,.0f}"),
    ("Median retail fill vs Tessera's own quote", f"{retail['quote_to_fill_bps'].median():.2f} bps worse"),
    ("Median bot fill vs Tessera's own quote", f"{bots['quote_to_fill_bps'].median():.2f} bps"),
    ("Median retail fill vs fair pool price (majors)", f"{majors_retail['fill_vs_benchmark_bps'].median():.2f} bps worse"),
    ("Median bot fill vs fair pool price (majors)", f"{majors_bot['fill_vs_benchmark_bps'].median():.2f} bps"),
    ("Tessera quote vs fair pool price (majors, retail)", f"{majors_retail['quote_vs_benchmark_bps'].median():.2f} bps better"),
    ("Best aggregator for the user", f"{best} ({agg.loc[best, 'p50']:.2f} bps)"),
    ("Worst aggregator for the user", f"{worst} ({agg.loc[worst, 'p50']:.2f} bps)"),
    ("Median user slippage tolerance (single-leg orders)", f"{bound['user_slippage_bps'].median():.0f} bps"),
    ("USD extracted from retail in window (quote-to-fill)", f"${retail['extracted_usd'].sum():,.0f}"),
    ("Estimated USD extracted over full history", f"${est['estimated_extracted_usd'].sum():,.0f}"),
    ("Keeper updates in first 10% / last 20% of block", f"{top:.0%} / {bottom:.0%}"),
    ("Regimes detected (retail fill vs fair)", "; ".join(f"{r.label} {r.start:%d %b}–{r.end:%d %b} ({r.retail_fill_vs_fair_p50:+.1f} bps)" for r in regimes.itertuples())),
    ("Keeper cadence change points (independent of fill quality)", ", ".join(c.date().isoformat() for c in cadence_cuts) or "none"),
    ("Days with no main-keeper updates", ", ".join(d.date().isoformat() for d in silent) or "none"),
    ("Keeper top-of-block offset share, skim vs honest regimes", f"{offset_by_regime.reindex(skim_rows['regime_id']).median():.0%} vs {offset_by_regime.reindex(honest_rows['regime_id']).median():.0%}" if len(honest_rows) else "no honest regime detected"),
    ("Markout 30 blocks after fill, retail vs bot (median)", f"{m30.get('retail', float('nan')):+.2f} vs {m30.get('bot', float('nan')):+.2f} bps"),
    ("Mid drift 30 blocks after fill, retail vs bot (median)", f"{d30.get('retail', float('nan')):+.2f} vs {d30.get('bot', float('nan')):+.2f} bps"),
    ("Retail shortfall vs fair on majors / bot gain vs fair", f"${retail_ledger.sum():,.0f} / ${-bot_ledger.sum():,.0f} (not a transfer, see text)"),
    ("Affected recipient keys", f"{len(affected):,} of {len(wallets):,}; median ${affected['shortfall_vs_quote_usd'].median():,.2f} over {affected['trades'].median():.0f} trades; {share_once:.0%} traded once"),
], columns=["Metric", "Value"])
display(headline.style.hide(axis="index"))
""")

nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
}
out = str(Path(__file__).resolve().parents[2] / "docs" / "source" / "tutorials" / "tessera-propamm-slippage.ipynb")
nbf.write(nb, out)
print("wrote", out, len(cells), "cells")

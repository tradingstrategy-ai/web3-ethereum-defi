# Monthly vault report chart visual identity plan

## Goal

Make the monthly "best-performing stablecoin vaults" blog report charts and
tables recognisably Trading Strategy branded, more informative and more
shareable. Today the charts are plain Plotly output. They share nothing with the
dark, rounded, green-accented look of the
[vault dashboard](https://tradingstrategy.ai/trading-view/vaults), and nothing
identifies a chart as ours once it is screenshotted onto social media.

This plan covers recommendations 1-12 from the PR #1600 review discussion:

| # | Recommendation | Phase |
|---|---|---|
| 1 | Dark "website" chart theme | 1 |
| 2 | Branded frame: rounded panel, glow, watermark, source footer | 1 |
| 3 | US Treasury bill benchmark on return charts | 2 |
| 4 | Glowing line and area styling for charts with few series | 1 |
| 5 | Display capped returns as `>9,999%` | 1 |
| 6 | 1200×630 hero / social chart, also usable as the feature image | 3 |
| 7 | Chain and protocol logos in charts instead of legend boxes | 3 |
| 8 | Risk/return bubble scatter | 3 |
| 9 | Month-over-month top 20 movers chart | 3 |
| 10 | Stacked TVL by protocol "state of the market" chart | 3 |
| 11 | Sparklines in tables | 2 |
| 12 | Risk badges in tables | 2 |

Out of scope: changing vault selection, rankings or metrics; the website
frontend; Ghost theme CSS.

## Current state

- Charts: `eth_defi/vault_report/charts.py` makes four Plotly figures:
  `chain_yields`, `best_rolling`, `low_volatility_rolling` and `correlation`.
  Kaleido renders them at 1400×800 or taller, on a white background. The
  categorical palette is validated for colour vision deficiency.
- Tables: `eth_defi/vault_report/sections.py` renders HTML tables with inline
  `text-align` styles, inside Ghost HTML cards.
- Rendering: headless Chrome through Kaleido. The font falls back to DejaVu
  Sans, because neither Inter nor Neue Haas Grotesk is installed. Chrome is
  found through `BROWSER_PATH` or `~/.local/share/choreographer`.
- Data available without new infrastructure:
  - Top vaults JSON: `risk`, `flags`, `strategy_tags`, `protocol_slug`,
    `vault_slug`, `chain`, `current_nav`, period metrics, `three_months_volatility`.
  - Pro price Parquet: `share_price` and `total_assets` history.
  - Protocol logos in this repo: `eth_defi/data/vaults/formatted_logos/{slug}/{light,dark,generic}.png`, 314 protocols.
  - Chain logos from the website: `https://tradingstrategy.ai/logos/blockchains/{slug}`.
  - Sparklines, public: `https://vault-sparklines.tradingstrategy.ai/sparkline-90d-{vault_id}.svg|png`.
  - Previous report HTML through the Ghost Content API.

## Brand reference (from `~/code/frontend`)

| Token | Value | Source |
|---|---|---|
| Dark body background | `hsl(60 2% 10%)` ≈ `#1a1a19` | `src/lib/components/css/color.css:35-53` |
| Text / light / extra-light | `hsl(36 12% 99%)`, ≈ `#e2e0de`, ≈ `#a5a3a1` | same |
| Bullish / bearish | ≈ `#22b453` / ≈ `#f97676`; logo candles `#22B554` / `#F62F2F` | `color.css`, `src/lib/assets/brand-mark.svg` |
| Chart axis and border | `#64748b`; split lines `rgba(148,163,184,0.18)` | `src/lib/echarts/HistoricalTvlGroupChart.svelte:435-516` |
| Axis labels | `#e2e8f0`, 13 px, weight 600; axis titles `#ffffff`, 16 px, weight 600 | same |
| Benchmark lines | amber `#fbbf24` | `src/lib/echarts/cumulative-tvl-apy.ts:4-5` |
| Multi-series palette | `protocolPalette`, 20 colours | `src/lib/scatter-plot/helpers.ts:296-316` |
| Watermark | horizontal logo recoloured `#d5deea` at 7% opacity, 224 px, top-left of plot area | `src/lib/echarts/watermark.ts:20-65` |
| Panel | radius `1.5rem`, green top-left radial glow `color-mix(--c-bullish, transparent 90%) 0% → transparent 20%`, 1 px inset highlight | `HistoricalTvlGroupChart.svelte:753-800` |
| Line glow | 3-3.5 px line, 18 px shadow blur, 11 px underlay at 18%, fill `rgba(74,222,128,0.34)` → `rgba(22,163,74,0.03)` | `src/lib/echarts/CumulativeTvlApyChart.svelte:258-351` |
| Social card | 1200×630, navy `#172554`, logo `#d5deea` | `src/lib/social-card/render.ts:3-45` |
| Fonts | Neue Haas Grotesk Display/Text (licensed), Source Serif Pro, Source Code Pro | `src/lib/components/css/typography-new.css:3-8` |

## Design

### Module layout

| Module | Responsibility |
|---|---|
| `eth_defi/vault_report/theme.py` (new) | `ChartTheme` dataclass with `DARK_THEME` and `LIGHT_THEME` presets. `apply_theme(fig, theme)` replaces today's `_apply_layout()` |
| `eth_defi/vault_report/branding.py` (new) | Pillow post-processing: rounded panel, glow, watermark, footer. Plus the hero image |
| `eth_defi/vault_report/assets/` (new) | `logo-horizontal.svg` and `brand-mark.svg` copied from the frontend, plus PNG rasters. Optional OFL font files, see open questions |
| `eth_defi/vault_report/logos.py` (new) | Loads protocol logos from `formatted_logos` and chain logos over HTTP, with a disk cache. Returns base64 data URIs for Plotly `layout.images` |
| `eth_defi/vault_report/benchmarks.py` (new) | `fetch_treasury_bill_rates()`, cached |
| `eth_defi/vault_report/movers.py` (new) | Parses the previous report's top table and computes rank changes |
| `charts.py`, `sections.py`, `report.py`, `post.py` | Use the above. Add new figures and table columns |

Every chart keeps two rendering stages: Plotly → Kaleido PNG, then an optional
Pillow composite. Plotly cannot draw rounded paper corners, radial glows or
footers with its own layout, so these are composited afterwards. This keeps the
Plotly figures reusable in notebooks.

### 1. Dark "website" chart theme

- `ChartTheme` (`dataclass(slots=True, frozen=True)`) fields: `surface`,
  `plot_surface`, `text`, `muted_text`, `axis`, `grid`, `series_colours`,
  `positive`, `negative`, `benchmark`, `diverging_scale`, `font_family`,
  `title_size`, `label_size`.
- `DARK_THEME` uses the brand tokens above. `LIGHT_THEME` is today's style.
  The environment variable `CHART_THEME=dark|light` selects one, default `dark`
  (decision pending, see open questions).
- The categorical palette must be revalidated on the dark surface: run the
  dataviz skill's `validate_palette.js --mode dark` with background `#1a1a19`.
  Keep hue order; take dark steps from the reference palette (`#3987e5
  #d95926 #199e70 #c98500 #d55181 #008300 #9085e9 #e66767`) rather than the
  frontend's 20-colour `protocolPalette`, which has adjacent pairs that are not
  colour-vision-deficiency safe. Document the deviation.
- Correlation heatmap on dark: diverging scale red `#e66767` - neutral
  `#383835` - blue `#3987e5`. Cell text white or black depending on cell
  luminance.
- Y axis on the right, as on the website charts. Titles left-aligned in the
  panel header (item 2) instead of centred in the plot.
- Tests: `apply_theme()` sets the expected layout values, and each theme
  renders a PNG whose corner pixel equals `surface`.

### 2. Branded frame

`branding.compose_chart_panel(chart_png, theme, title, subtitle, footer) -> PNG`:

- A canvas slightly larger than the chart, filled with the page background
  (`#1a1a19`). On it, a rounded rectangle panel (radius 48 px at 2× scale)
  with a 1 px highlight border.
- A green radial glow in the top-left corner (`#22b453` at 10% alpha fading
  to 0 over 20% of the panel width), drawn with Pillow `ImageDraw` and a
  Gaussian blur.
- A header with the title (32 px, white, weight 600) and a subtitle (muted),
  for example "Top 8 vaults by 1M return, ≥ $200k TVL".
- A watermark: `logo-horizontal.svg` rasterised once, recoloured `#d5deea`
  at 7% opacity, top-left inside the plot area. The Plotly layout image can
  also do this.
- A footer: brand mark, "tradingstrategy.ai · Data 2026-09-25", and a short
  URL to the matching live chart page on the right, for example
  `tradingstrategy.ai/vaults/chains`.
- SVG rasterisation: use Chrome, which is already required, through Plotly
  `layout.images` with an SVG data URI. Alternatively pre-render the PNGs once
  and commit them to `assets/`. The latter is preferred: no `cairosvg`
  dependency.
- Output width stays 1400 px. Height grows by the header and footer.
- Tests: output dimensions; corner pixels transparent or background; footer
  text present (checked by rendering a known string — no OCR, just
  deterministic pixel hashes of the footer region for a fixed input).

### 3. US Treasury bill benchmark

- Data: FRED `DTB3` (3-month T-bill secondary market rate, daily), CSV export
  `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3`, no API key.
  The frontend uses FRED plus the Treasury Fiscal Data API with a file
  cache, because FRED rate-limits; mirror that.
  `fetch_treasury_bill_rates(cache_dir, max_age=1 day) -> pd.Series` (percent,
  daily). On failure, log a warning and return the cached copy or `None`. The
  chart then omits the benchmark; the report is never aborted over it.
- Rolling return charts: convert the rate series to a 90-day compounded
  return, `prod(1 + r_d/365)` over the window, and draw it as a dashed amber
  (`#fbbf24`) line labelled "US 3M T-bill". The same 90-day window as the
  vault lines keeps it comparable.
- Chain yield chart: a vertical dashed amber line at the latest T-bill yield,
  labelled "US T-bill 4.1%".
- Correctness: DTB3 is a discount rate on a bank-discount basis. The
  difference from the investment yield is small but should be noted in the
  chart note, or converted: `y = 365*d/(360 - 91*d)`.
- Tests: conversion on a fixed series, cache fallback when the HTTP fetch
  raises, and the chart skips the line when the series is `None`.
- Real integration test: fetch DTB3 and assert the latest value is between 0
  and 20.

### 4. Glowing line and area styling

- For charts with one to three series (hero, T-bill comparison, single-vault
  highlights), draw each series twice: a wide underlay (11 px, 18% alpha) and
  a crisp line (3.5 px). Add an area fill with a vertical gradient. Plotly
  scatter `fill="tozeroy"` with `fillgradient` supports vertical gradients;
  the installed Plotly 6.6.0 has it.
- Do **not** apply the glow to 8-line charts: it adds clutter. The dataviz
  skill's marks spec asks for thin lines when there are many series.
- Tests: figure trace counts and fill settings.

### 5. Capped returns as `>9,999%`

- The export caps annualised returns at 100.0 (10,000%). In
  `sections.format_return()`, show values ≥ 99.99 as `>9,999% (n|g)`, matching
  the website table. Also in chart hover and labels where used.
- Document in the table notes: "Annualised returns above 9,999% are capped".
- Tests: 100.0 → `>9,999% (n)`, 99.0 → `9,900.0% (n)`.

### 6. Hero / social image (1200×630)

- `branding.render_hero_image(top_vaults_df, month_label, theme) -> PNG`.
  Layout: the title "Best stablecoin vaults · September 2026", then the top 5
  yield vaults as horizontal rounded bars with protocol logos, vault names,
  big annualised return numbers and a chain chip, followed by the brand mark
  and URL.
- Rendered with Plotly and composited with Pillow, like item 2, or entirely
  with Pillow; pick whichever gives crisper text.
- Upload it as the Ghost `feature_image` in `publish_report_draft()`. Ghost
  Admin API posts accept `feature_image`, `og_image` and `twitter_image`.
- The top 5 must exclude capped returns and flagged vaults, so the hero never
  shows `>9,999%`. Use `chart_max_return` from `ReportCriteria`.
- Tests: size is exactly 1200×630 and the post payload contains `feature_image`.

### 7. Logos in charts

- `logos.py`:
  - `load_protocol_logo(slug, variant="dark"|"light")` reads
    `formatted_logos/{slug}/{variant}.png`, falls back to `generic.png`, and
    returns `None` when there is no logo.
  - `fetch_chain_logo(slug)` downloads from
    `https://tradingstrategy.ai/logos/blockchains/{slug}` into
    `{cache_dir}/logos/`. Chain slugs come from
    `vault_metrics._get_chain_slug()`.
- Chain yield chart: a 28 px chain logo left of each bar label, placed with
  `layout.images` in `xref="paper"`, `yref="y"` coordinates.
- Rolling return charts: replace the legend box with end-of-line labels,
  "logo + short vault name", at each line's last point. The dataviz skill
  recommends direct labels for ≤ 4 series. For 5-8 series, keep the legend
  but add the protocol logo in front of each entry. That needs a custom
  legend drawn with annotations and images, because Plotly legends cannot
  hold images. Resolve label collisions with a simple vertical repel pass
  (sort by y, enforce a minimum gap).
- Tests: logo lookup fallbacks; the number of `layout.images` equals the
  number of bars with logos.

### 8. Risk/return bubble scatter

- x: annualised 3M volatility (log scale). y: 3M annualised return, clipped at
  `chart_max_return`. Bubble area ∝ current TVL. Colour by strategy
  category (`strategy_tags` → the exported `categories`), limited to the
  top 7 categories + "Other" to respect the 8-colour palette.
- Do not use Sharpe on an axis: near-zero-volatility lending vaults have
  Sharpe in the millions.
- Population: the `best` section candidates before truncation (≥ $200k TVL,
  yield vaults), maybe 300-800 points. Label only the top 10 by return
  directly, with names.
- A reference T-bill horizontal line (item 3).
- Placement: a new "Risk and return" section after "The best-performing
  vaults", with a criteria note.
- Tests: category folding into "Other" and clipping.

### 9. Month-over-month movers

- `movers.py`:
  - `parse_previous_ranking(previous_post_html, section_heading_id) -> list[VaultRef]`
    reads the `<table>` after the heading. Old links look like
    `/trading-view/{chain}/vaults/{slug}?a={address}`; new links look like
    `/trading-view/vaults/{slug}`. Resolve each to a vault id using the
    address when present, else `vault_slug` in the current JSON.
  - `calculate_rank_changes(previous, current_top_n) -> DataFrame` with the
    columns new / up / down / same / dropped.
- Store the ranking in `report.json` as `rankings: {section: [vault_id, ...]}`,
  so future months can diff without parsing HTML. Parsing HTML is only the
  bootstrap for the first automated month.
- Chart: a slope chart of the top 20, with the previous rank on the left and
  the current rank on the right. New entries are highlighted in brand green;
  dropped vaults are listed in a small table. Plus a "New in top 20" table
  column or badge.
- Edge cases: the previous report is from months ago (February 2026 → September
  2026). Show the time gap in the subtitle. Vaults renamed since then are
  matched by address.
- Tests: parsing the February 2026 HTML fixture (store a trimmed copy under
  `tests/vault_report/fixtures/`); slug and address resolution; rank-change
  maths.

### 10. Stacked TVL by protocol

- Data: the Pro price Parquet `total_assets` column for stablecoin vaults,
  resampled weekly (last value), summed per protocol. That is a projected read
  of `id`, `timestamp` and `total_assets` for about 10M rows; check memory
  (expect < 1 GB) and time.
- Denomination: stablecoin vaults are USD-like. EUR vaults need conversion, or
  must be excluded; check how the website historical TVL chart handles this
  and match it.
- Clean the data with the same flags as the website: exclude blacklisted
  vaults and `abnormal_tvl` flags, and cap at `MAX_VALID_NAV`. Otherwise one
  broken share token dominates the stack.
- Top 10 protocols by current TVL + "Other", stacked areas at 0.56 alpha with
  no stroke, 1 year of history.
- The numbers must match `https://tradingstrategy.ai/trading-view/vaults/historical-tvl-protocol?history=1y`
  within a few percent. Add a manual verification step to the PR.
- Tests: aggregation on a synthetic Parquet.

### 11. Sparklines in tables

- A new table column "3M" (first after the vault name) with
  `<img src="https://vault-sparklines.tradingstrategy.ai/sparkline-90d-{id}.svg" width="72" height="18" alt="">`.
- Check existence before rendering: HEAD requests with a small thread pool,
  since at most about 250 unique vaults appear. Leave the cell empty when the
  sparkline is missing; low-TVL vaults are rendered on a 72-hour cadence and
  may be missing.
- Ghost HTML cards keep the `<img>` unchanged. Confirm that the Ghost theme
  does not force `img { width: 100% }` inside tables; if it does, add an
  inline `style="width:72px;max-width:none"`.
- Hotlinking: the sparkline bucket is public and already used by the website;
  check caching headers and CORS (not needed for `<img>`).
- Tests: HTML contains the image for known ids and none for missing ones,
  with mocked HEAD responses.

### 12. Risk badges

- A new column "Risk" with a pill, using the `risk` field (Negligible, Low,
  High, Dangerous, Severe; blacklisted vaults are already excluded):
  `<span style="padding:1px 8px;border-radius:999px;background:{bg};color:{fg};font-size:12px">Low</span>`.
- Colours follow the dataviz status palette, with an icon and label so colour
  is never the only signal: Negligible/Low good `#0ca30c`, High warning
  `#fab219`, Dangerous serious `#ec835a`, Severe critical `#d03b3b`. Check the
  text contrast of each pill.
- Link each badge to the
  [technical risk framework post](https://tradingstrategy.ai/blog/announcing-vault-technical-risk-framework-beta).
- Unknown risk (`None`) shows "Unrated" in grey.
- Table width: adding "3M" and "Risk" pushes past the blog column width.
  Drop "Lifetime return" from the main tables, or render the tables as
  horizontally scrollable (`<div style="overflow-x:auto">`). Decide after a
  Ghost preview.

## Constraints and invariants

- No numbers change: charts and tables must show exactly the data the tables
  show today. Visual changes only, apart from the new charts.
- Report generation must not fail because of optional assets. Missing logos,
  a FRED outage or missing sparklines degrade to the current output with a
  warning.
- No new required credentials. FRED and the sparkline and logo endpoints are
  public.
- New dependencies only when unavoidable. Pillow is already installed;
  `cairosvg` is avoided by committing pre-rasterised brand PNGs.
- Deterministic output for the same inputs (fixed fonts, no timestamps inside
  images apart from the data date), so regenerated charts can be diffed.
- Follow `CLAUDE.md`: `fetch_` prefixes for network reads, Sphinx docstrings,
  UK English, heading-case chart titles, module-level imports, naive UTC.

## Tests and acceptance

- Unit tests for each new pure function, listed per item above.
- Image tests: dimensions, background pixel colours and the absence of
  exceptions. No brittle whole-image golden comparisons; use a region hash
  only for deterministic Pillow-drawn elements such as the footer.
- Real integration tests, guarded and skipped without network: FRED DTB3,
  chain logo endpoint, sparkline HEAD.
- Visual acceptance: generate a contact sheet of all charts in both themes
  and post it as a PR comment. Create a Ghost draft once the Admin key exists,
  and check it in the actual blog theme on desktop and mobile widths.
- Performance: the full report run stays under 60 seconds, excluding the
  first download.

## Implementation sequence

1. **Phase 1, identity** (items 1, 2, 4, 5): `theme.py`, `branding.py`,
   assets, and dark palette validation. One PR with before/after images.
2. **Phase 2, context** (items 3, 11, 12): benchmark fetcher, table columns,
   Ghost table-width check.
3. **Phase 3, new visuals** (items 6, 7, 8, 9, 10): hero image and feature
   image upload first (highest sharing value), then logos, scatter, movers
   and the stacked TVL chart. Items 9 and 10 are independent and can ship
   separately.

## Open questions

1. **Dark or light default.** The blog page is light, and dark charts will
   look like framed cards on it. The website look argues for dark.
2. **Font.** Neue Haas Grotesk is licensed and cannot be bundled. Options:
   - bundle Inter (OFL) and register it with fontconfig for Kaleido's Chrome,
     which needs a font install step on the machine or Docker image that
     renders reports;
   - accept a system font;
   - render the text-heavy parts (headers, footers, hero) with Pillow using a
     bundled TTF, which works without fontconfig.
3. **Where the brand assets live.** Copying the SVG logos from the frontend
   repo means they can drift; alternatively fetch
   `https://tradingstrategy.ai/...` at runtime.
4. **Table width** with the two new columns in the Ghost theme.
5. **Stacked TVL data parity** with the website chart: which vault set and
   currency conversion the frontend uses.

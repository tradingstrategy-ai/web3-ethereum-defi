# Monthly best-performing stablecoin vaults report

Automates the monthly "The best-performing stablecoin vaults" posts on the
[Trading Strategy blog](https://tradingstrategy.ai/blog). The last manually
produced edition was
[February 2026](https://tradingstrategy.ai/blog/the-best-performing-stablecoin-vaults-february-2026),
built by copy-pasting output from the
`docs/source/tutorials/erc-4626-vault-report-markdown.ipynb` notebook.

The pipeline generates the data-driven parts: tables, branded charts, a social
hero image and statistics. The editor writes the monthly news and commentary in
Ghost Admin and publishes the post.

## Running

```shell
source .local-test.env && poetry run python scripts/erc-4626/generate-monthly-vault-report.py
```

A full run, including downloads, took about 15 seconds on 2026-09-25.
Downloads younger than six hours are reused. The script:

1. Downloads the public top vaults JSON (`https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json`).
   This is the same data the [vault dashboard](https://tradingstrategy.ai/trading-view/vaults) renders, so the report numbers match the website.
2. Downloads the ~250 MB cleaned vault price Parquet through the Pro
   [vault datasets](https://tradingstrategy.ai/trading-view/vaults/datasets) API
   using `VAULT_PRO_API_KEY`. The charts use it.
3. Downloads the 3-month US Treasury yield ([FRED DGS3MO](https://fred.stlouisfed.org/series/DGS3MO)),
   the same benchmark series as the website's vault pages, and chain logos from
   the website. Both are optional: an outage leaves them out of the charts.
4. Reads the previous report post with the Ghost Content API
   (`GHOST_CONTENT_API_URL`, `GHOST_CONTENT_API_KEY`). The new post links back to it
   and copies its *About the report*, *Partners* and *Next steps* sections.
   Changelog entries added since its publication are offered to the editor.
   The movers chart compares against its ranking; the stored `report.json`
   ranking of the previous bundle is used when it exists, otherwise the ranking
   is parsed from the post's table.
5. Writes a local bundle to `~/.cache/tradingstrategy/vault-report/reports/{slug}/`:
   `post.html`, a browser-viewable `preview.html`, `report.json` (including the
   rankings for next month), `hero.png`, `hero-square.png`, `charts/*.png` and
   `tables/*.html|csv`. The `low_volatility` table is the input of the
   low-volatility chart and is not included in the post.
6. When `GHOST_ADMIN_API_KEY` is set, uploads the charts and the hero image and
   creates a **draft** post, with the hero as its feature image. The script never publishes.

See the script docstring for all environment variables.

### Ghost Admin API key

The Content API key is read-only and cannot create posts. To create drafts, add a
custom integration in Ghost Admin under *Settings → Integrations → Add custom
integration*. Store its Admin API key, which has the format `{id}:{secret}`, as
`GHOST_ADMIN_API_KEY`. The `GHOST_ADMIN_API_URL` defaults to `GHOST_CONTENT_API_URL`.

The script refuses to overwrite an existing draft with the same slug, because
that would lose the editor's work, and it never touches a published post. Delete
the draft, or set `GHOST_OVERWRITE_DRAFT=true`, to regenerate it.

As of 2026-09-25 no Admin API key was available, so draft creation and the
feature image upload are covered only by mocked tests. After adding the key, run
`test_ghost_admin_api_draft` (see [Tests](#tests)). The blog frontend renders only
published posts, so check the first branded post right after publishing: the
tables, the image cards and the link previews on X and LinkedIn.

### Charts and visual identity

Charts follow the dark look of the website's vault pages. The blog itself is
rendered by the website frontend in dark mode, so the charts sit naturally on
the page. `CHART_THEME=light` switches to a light theme, e.g. for newsletters.

- **Rendering:** Plotly figures are rendered by Kaleido (headless Chrome), then
  framed with Pillow: a rounded panel with a title header, a green corner glow
  and a footer carrying the brand, the data date and a link to the live chart.
  A faint logo watermark sits inside the plot area.
- **Font:** the bundled [Inter](https://rsms.me/inter/) (SIL Open Font Licence)
  font is used both by Chrome, through a private `FONTCONFIG_FILE`, and by Pillow.
  Nothing is installed system-wide.
- **Chrome:** install it with `poetry run plotly_get_chrome`, or point
  `BROWSER_PATH` at an existing Chrome binary. Set `RENDER_CHARTS=false` to skip the charts.
- **Brand assets:** `logo-horizontal.svg` and `brand-mark.svg` in
  `eth_defi/vault_report/assets` are copied from the frontend
  (`src/lib/assets`); re-copy them if the brand changes. Protocol logos come
  from `eth_defi/data/vaults/formatted_logos`.
- **Palettes:** eight fixed-order categorical colours, checked for colour vision
  deficiency on both theme surfaces; see `eth_defi.vault_report.theme`.

Chrome rasterisation is not bit-stable across Chrome versions or machines, so
image tests check sizes and pixel alpha rather than whole-image hashes.

### Using local pipeline data

Where the vault pipeline data directory is available, the script can read the
input files from it instead of downloading them. The directory is
`~/.tradingstrategy/vaults`, or `PIPELINE_DATA_DIR` when set; see
`eth_defi.vault.vaultdb.get_pipeline_data_dir()`. The script only reads these files:

```shell
TOP_VAULTS_JSON=~/.tradingstrategy/vaults/top_vaults_by_chain.json \
VAULT_PRICES_PARQUET=~/.tradingstrategy/vaults/cleaned-vault-prices-1h.parquet \
poetry run python scripts/erc-4626/generate-monthly-vault-report.py
```

## Report content

The sections follow the February 2026 post, with the changes noted below.
"Yield vaults" excludes perp DEX vaults and vaults with fewer than 10 deposit
and redemption events.

| Section | Content |
|---|---|
| Hero image | New. The top 5 yield vaults as bars with protocol logos: 1200×630 for link previews and the Ghost feature image, and a 1080×1080 version for X, which shows blog links as square cards. Leaves out vaults above 400% annualised return, above 50% volatility or with a Severe or Dangerous risk rating |
| Average vault yield per blockchain | New chart, replacing a website screenshot. TVL-weighted 1M annualised return per chain with chain logos and the T-bill yield line, perp DEX vaults included. Excludes outlier vaults (>400% ann.) and volatile vaults (>50% ann. volatility) |
| Stablecoin vault TVL by protocol | New chart. Weekly TVL over 12 months: top 7 protocols and Other, with a glowing total line. Built with the same DuckDB query and filters as the website's historical TVL chart (blacklisted vaults and points above $50B excluded) |
| The best-performing vaults | Yield vaults with ≥ $200k TVL, top 50 by 1M annualised return, with a caption counting the vaults that beat the T-bill. Two 3M rolling return charts, top vaults and low-volatility vaults, against the T-bill |
| Top movers since the previous report | New chart. Slope chart of the top 20. Previous ranks are recalculated among vaults eligible this month |
| Risk and return | New chart. Bubble scatter of 3M volatility (log scale) against 3M annualised return, bubble area by TVL, coloured by strategy. Returns above 100% are drawn as triangles on the top edge |
| The best-performing perp DEX vaults | New section. Hyperliquid, GRVT, Lighter and other native trading vaults with ≥ $200k TVL, top 20. In earlier posts these crowded yield vaults out of the main list |
| Correlation of returns | Daily returns correlation heatmap over 90 days: top 20 vaults by 3M return with ≥ $50k TVL, at most two per protocol |
| The best-performing vaults on each chain | Top 3 per chain with ≥ $100k TVL, perp DEX vaults included |
| The best-performing large vaults | Yield vaults with ≥ $2M TVL, top 50 |
| The best-performing new vaults | Yield vaults launched in the last 60 days with ≥ $15k TVL, top 50 |

All listings exclude blacklisted vaults and vaults whose data is more than a
week older than the report date. Rolling return charts leave out vaults above
400% annualised return or 50% annualised volatility, because one outlier
flattens every other line. These vaults are still listed in the tables.

### Tables

- Returns are annualised: (n) net of fees, (g) gross when fee data is not
  available. The export caps annualised returns at 10,000%, shown as `>9,999%`
  like on the website; ties are ranked by the absolute one-month return.
- "3M price" shows the website's published 90-day sparkline (PNG, so it
  survives newsletter email clients). Low-TVL vaults may not have one yet.
- "Risk" shows the technical risk rating as a coloured pill linking to the
  [risk framework](https://tradingstrategy.ai/blog/announcing-vault-technical-risk-framework-beta).
  Unrated vaults show muted text.

The thresholds live in `eth_defi.vault_report.sections.ReportCriteria`.

## Editor workflow

1. Run the script in the last week of the month.
2. Open the draft in Ghost Admin using the editor link the script prints.
3. Fill in the yellow `EDITOR:` callouts: the intro highlight, report content
   updates (changelog candidates are listed in the callout), community news and
   comments on the top vaults and the TVL trend. Then delete the callouts.
4. Review the tables. Unusual entries, such as capped `>9,999%` returns or
   leveraged tokens, deserve a comment or a vault note.
5. Publish, then share the post. Attach `hero-square.png` when posting on X.

## Tests

```shell
source .local-test.env && poetry run pytest tests/vault_report
```

`tests/vault_report/test_vault_report.py` runs offline with synthetic data.
`tests/vault_report/test_vault_report_live.py` checks the real data sources (top
vaults JSON, Pro prices, FRED, chain logos) and the Ghost APIs. Each test is
skipped when its credentials are missing. The Admin API test uploads a 1×1
pixel image, creates a draft and then deletes the draft. Ghost has no API to
delete uploaded images, so the test image stays in the media library.

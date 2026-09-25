# Monthly best-performing stablecoin vaults report

Automates the monthly "The best-performing stablecoin vaults" posts on the
[Trading Strategy blog](https://tradingstrategy.ai/blog). The last manually
produced edition was
[February 2026](https://tradingstrategy.ai/blog/the-best-performing-stablecoin-vaults-february-2026),
built by copy-pasting output from the
`docs/source/tutorials/erc-4626-vault-report-markdown.ipynb` notebook.

The pipeline generates the data-driven parts: tables, charts and statistics.
The editor writes the monthly news and commentary in Ghost Admin and publishes the post.

## Running

```shell
source .local-test.env && poetry run python scripts/erc-4626/generate-monthly-vault-report.py
```

A full run, including both downloads, took about 15 seconds on 2026-09-25.
Downloads younger than six hours are reused. The script:

1. Downloads the public top vaults JSON (`https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json`).
   This is the same data the [vault dashboard](https://tradingstrategy.ai/trading-view/vaults) renders, so the report numbers match the website.
2. Downloads the ~250 MB cleaned vault price Parquet through the Pro
   [vault datasets](https://tradingstrategy.ai/trading-view/vaults/datasets) API
   using `VAULT_PRO_API_KEY`. Only the charts use it.
3. Reads the previous report post with the Ghost Content API
   (`GHOST_CONTENT_API_URL`, `GHOST_CONTENT_API_KEY`). The new post links back to it
   and copies its *About the report*, *Partners* and *Next steps* sections.
   Changelog entries added since its publication are offered to the editor.
4. Writes a local bundle to `~/.cache/tradingstrategy/vault-report/reports/{slug}/`:
   `post.html`, a browser-viewable `preview.html`, `report.json`, `charts/*.png`
   and `tables/*.html|csv`. The `low_volatility` table is the input of the
   low-volatility chart and is not included in the post.
5. When `GHOST_ADMIN_API_KEY` is set, uploads the charts and creates a **draft**
   post. The script never publishes.

See the script docstring for all environment variables.

### Ghost Admin API key

The Content API key is read-only and cannot create posts. To create drafts, add a
custom integration in Ghost Admin under *Settings → Integrations → Add custom
integration*. Store its Admin API key, which has the format `{id}:{secret}`, as
`GHOST_ADMIN_API_KEY`. The `GHOST_ADMIN_API_URL` defaults to `GHOST_CONTENT_API_URL`.

The script refuses to overwrite an existing draft with the same slug, because
that would lose the editor's work, and it never touches a published post. Delete
the draft, or set `GHOST_OVERWRITE_DRAFT=true`, to regenerate it.

As of 2026-09-25 no Admin API key was available, so draft creation is covered only
by mocked tests. After adding the key, run `test_ghost_admin_api_draft` (see
[Tests](#tests)) and check that the first generated draft shows the tables as
HTML cards and the `EDITOR:` notes as callouts in the Ghost editor.

### Chart rendering

Kaleido 1.x renders the PNG charts with a headless Chrome. Install Chrome with
`poetry run plotly_get_chrome`, or point `BROWSER_PATH` at an existing Chrome
binary. Set `RENDER_CHARTS=false` to skip the charts.

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

| Section | Selection |
|---|---|
| Average vault yield per blockchain | New chart, replacing a website screenshot. TVL-weighted 1M annualised return per chain, perp DEX vaults included. Excludes outlier vaults (>400% ann.) and volatile vaults (>50% ann. volatility) |
| The best-performing vaults | Yield vaults with ≥ $200k TVL, top 50 by 1M annualised return. Two 3M rolling return charts: top vaults and low-volatility vaults |
| The best-performing perp DEX vaults | New section. Hyperliquid, GRVT, Lighter and other native trading vaults with ≥ $200k TVL, top 20. In earlier posts these crowded yield vaults out of the main list |
| Correlation of returns | Daily returns correlation heatmap over 90 days: top 20 vaults by 3M return with ≥ $50k TVL, at most two per protocol |
| The best-performing vaults on each chain | Top 3 per chain with ≥ $100k TVL, perp DEX vaults included |
| The best-performing large vaults | Yield vaults with ≥ $2M TVL, top 50 |
| The best-performing new vaults | Yield vaults launched in the last 60 days with ≥ $15k TVL, top 50 |

All listings exclude blacklisted vaults and vaults whose data is more than a
week older than the report date. Returns are net of fees (n) when fee data is
known and gross (g) otherwise. The export caps annualised returns at 10,000%;
ties are ranked by the absolute one-month return. Rolling return charts leave
out vaults above 400% annualised return, because one outlier flattens every
other line. These vaults are still listed in the tables.

The thresholds live in `eth_defi.vault_report.sections.ReportCriteria`.

## Editor workflow

1. Run the script in the last week of the month.
2. Open the draft in Ghost Admin using the editor link the script prints.
3. Fill in the yellow `EDITOR:` callouts: the intro highlight, report content
   updates (changelog candidates are listed in the callout) and community news.
   Then delete the callouts.
4. Review the tables. Unusual entries, such as capped 10,000% returns or
   leveraged tokens, deserve a comment or a vault note.
5. Add a feature image and publish.

## Tests

```shell
source .local-test.env && poetry run pytest tests/vault_report
```

`tests/vault_report/test_vault_report_live.py` checks the real data sources and
the Ghost APIs. Each test is skipped when its credentials are missing. The Admin
API test uploads a 1×1 pixel image, creates a draft and then deletes the draft.
Ghost has no API to delete uploaded images, so the test image stays in the media library.

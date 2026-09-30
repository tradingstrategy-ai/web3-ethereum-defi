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

Without the investability check, a run with cached downloads took about 40
seconds on 2026-09-29, most of it rendering the 16 charts. With the check,
which runs an LLM agent, a run took about 32 minutes on 2026-09-26, see
[Investability check](#investability-check). Downloads younger than six hours
are reused. The script:

1. Downloads the public top vaults JSON (`https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json`).
   This is the same data the [vault dashboard](https://tradingstrategy.ai/vaults) renders, so the report numbers match the website.
2. Downloads the ~250 MB cleaned vault price Parquet through the Pro
   [vault datasets](https://tradingstrategy.ai/vaults/datasets) API
   using `VAULT_PRO_API_KEY`. The charts use it.
3. Downloads the benchmarks: the 3-month US Treasury yield
   ([FRED DGS3MO](https://fred.stlouisfed.org/series/DGS3MO)) and daily BTC and ETH
   closes from Coinbase, the same sources as the website's vault pages, plus chain
   logos from the website. All are optional: an outage leaves them out of the charts.
4. Reads the previous report post with the Ghost Content API
   (`GHOST_CONTENT_API_URL`, `GHOST_CONTENT_API_KEY`). The new post links back to it
   and copies its *About the report*, *Partners* and *Next steps* sections.
   Changelog entries added since its publication are offered to the editor.
   The same API reads the four latest
   [podcast](https://tradingstrategy.ai/podcast) episodes for the *Latest
   podcasts* section, see [Latest podcasts](#latest-podcasts).
5. Optionally runs the [investability check](#investability-check), which
   removes vaults that are not investable in practice from the rankings.
6. Writes a local bundle to `~/.cache/tradingstrategy/vault-report/reports/{slug}/`:
   `post.html`, a browser-viewable `preview.html`, `report.json`, `hero.png`,
   `hero-square.png`, `charts/*.png`, `tables/*.csv`, the podcast images in
   `podcasts/` and the investability check files `vault-check-*`.
7. When `GHOST_ADMIN_API_KEY` is set, uploads the charts, the podcast images
   and the hero image and creates a **draft** post, with the hero as its
   feature image. The script never publishes.

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

- **Rendering:** Plotly figures are rendered by Kaleido (headless Chrome) on a
  transparent background, then framed with Pillow: a rounded panel with a title
  header, a green corner glow and a footer carrying the brand, the data date
  and a link to the live chart. The chart is cropped to its drawn content and
  fitted to the panel's inner width, so every panel is 1400 px wide with a
  44 px margin on all four sides, whatever its Plotly margins. Keep new Plotly
  layouts within 5% of that inner width; the pipeline warns otherwise. These
  are design pixels: Kaleido and the frame render them at
  `branding.CHART_SCALE`, 4/3, so the exported PNGs are 1867 px wide and stay
  sharp on high-density screens. The social hero images keep their fixed
  1200×630 and 1080×1080 sizes.
  Wherever a chart names a vault (the hero image, performance chart legends and
  inflows and outflows), the curator, protocol and chain follow under the name
  in that order, each with its own icon. Logos are trimmed of their
  transparent margins (`charts.trim_logos()`) and each icon is sized to its
  mark, so every icon sits the same distance from its text. A curator that is the protocol itself,
  or a chain named after the protocol, is shown once. Titles, subtitles, legend
  entries and vault labels word-wrap instead of being truncated, so names are
  always shown in full.
- **Font:** the bundled [Inter](https://rsms.me/inter/) (SIL Open Font Licence)
  font is used both by Chrome, through a private `FONTCONFIG_FILE`, and by Pillow.
  Nothing is installed system-wide.
- **Chrome:** install it with `poetry run plotly_get_chrome`, or point
  `BROWSER_PATH` at an existing Chrome binary. Set `RENDER_CHARTS=false` to skip the charts.
- **Brand assets:** `logo-horizontal.svg` and `brand-mark.svg` in
  `eth_defi/vault_report/assets` are copied from the frontend
  (`src/lib/assets`); re-copy them if the brand changes. The panel footers
  and hero images use the TradingStrategy.ai logo,
  `logo-horizontal-ai.svg`: the website logo with a `.ai` suffix built from the
  wordmark's own glyphs. `scripts/erc-4626/render-vault-report-logo.py`
  rebuilds it and its PNG renders for the Pillow-drawn footers and heroes;
  rerun it after re-copying `logo-horizontal.svg`. The BTC, ETH and US
  Treasury logos in `assets/benchmarks` come from the frontend's
  `src/lib/assets/logos/tokens`, as used by its vault comparison chart; the
  performance charts show them in the legend and at the benchmark line ends.
  Protocol logos come from `eth_defi/data/vaults/formatted_logos`.
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

### Benchmarks

Each vault is compared with the benchmark that matches its activity, following
the website's rules (`vault-price-benchmarks.ts` in the frontend):

| Vault | Benchmark |
|---|---|
| Perpetual futures DEX vaults (Hyperliquid, GRVT, Lighter, Hibachi, ApeX), GMX GLV and crypto GM pools | BTC and ETH; GM BTC or ETH pools only their own asset |
| Other volatile vaults: 3M volatility ≥ 25% or 3M drawdown ≤ −10% | BTC and ETH |
| Calm yield vaults: lending, credit and GMX stablecoin-only pools | US 3M T-bill |

The website compares every non-perp, non-GMX vault with the T-bill. The report
adds the activity rule, because a trading-like stablecoin vault is better judged
against the crypto market it trades. Both thresholds are in `ReportCriteria`.

## Report content

The section order, selection rules and editor input of each section are
described in [README-blog-post-outline.md](./README-blog-post-outline.md). In
short, the post has:

- the four latest podcast episodes;
- average yield dot plots for the 10 largest blockchains, the 10 largest
  protocols by TVL and the 10 highest-yielding protocols with at least $150k
  TVL, against the T-bill;
- stablecoin TVL by DeFi vault protocol and by blockchain, and stablecoin NAV
  by tokenised fund, over 12 months;
- inflows and outflows: the largest 30-day TVL changes in dollars, by vault and by blockchain;
- the best-performing vaults, split into lending, real-world asset (RWA), perp
  DEX by return, perp DEX by Sharpe ratio and other vaults, and AMM pools, each
  with a performance chart and a table, and the best vaults on each chain;
- the best-performing tokenised funds and new vaults;
- a risk and return scatter;
- the vaults the investability check excluded.

The hero image shows the top 5 yield vaults with their curator, protocol and
chain, 90-day price sparklines and the return as a large number: 1200×630 for
link previews and the Ghost feature image, and a 1080×1080 version for X, which
shows blog links as square cards. It leaves out vaults above 400% annualised
return, above 50% volatility or with a Dangerous or worse risk rating; the
performance charts, the average yield charts and the risk and return chart leave
out Dangerous or worse vaults as well, while the tables keep them. The image does not state these
filters; its footer shows only the minimum TVL and the data date.

All listings exclude blacklisted vaults, rated Blacklisted in the export or
given a bad flag in `eth_defi/vault/flag.py` since, and vaults whose data is
more than a week older than the report date. Performance charts draw the 90-day equity
curves, in percent, of the top vaults of each group by three-month return in one
chart with a shared axis, so they can be compared directly. The legend and the
benchmark line ends show annualised returns over each line's span, computed
from the chart's daily prices and capped at >9,999% like the tables. The vaults
are chosen and ranked by the export's three-month return instead, so a legend
number can be lower than the one below it, e.g. for a vault younger than 90
days or one whose export metrics lag the price data. The legend
numbers are chart ranks, repeated as badges at the line ends. Days without a share price update are interpolated, so sparsely
updated vaults do not draw staircases. Vaults younger than 90
days start at 0% at launch. A single vault far
above the others is drawn off scale with a ▲ marker, and the axis switches to
a log scale when returns exceed 100%. The perp DEX Sharpe ratio chart draws the
90-day rolling Sharpe ratio on a scale starting at 0 instead, calculated from forward-filled daily prices
like the exported 3M Sharpe, so its latest values match the table.

### AMM pools

AMM pools, GMX GM and GLV pools and the Curve-based YieldBasis, are the vaults
with the `amm_pool_like` feature that the scanner sets. They earn trading fees
on pooled crypto, so their returns follow the price of the pooled assets. They get their own *AMM pools* section with at least $1M TVL
and are left out of every other ranking and chart by default, counted only in
the TVL summaries. Set `ReportCriteria.include_amm_pools` to rank them with
the other vaults. See
[README-blog-post-outline.md](./README-blog-post-outline.md#common-rules).

### Latest podcasts

The post lists the four latest episodes of the
[Trading Strategy podcast](https://tradingstrategy.ai/podcast) before the data
sections (`eth_defi.vault_report.podcasts`). Episodes are blog posts on Ghost,
selected like the website's podcast page: the latest published posts whose
title contains "episode". From each post the section takes:

- the title, linked to the blog post;
- the first paragraph, the episode's promotion text;
- the YouTube and Spotify links, without share-tracking parameters, each with
  the service's brand-coloured icon (`assets/podcast`, the website's footer
  icons rendered to PNG by `scripts/erc-4626/render-vault-report-logo.py`);
- the guest's logo, found from the post's link to the guest's curator or
  protocol page on the website, e.g. `/vaults/curators/yearn`.

Logos come from `eth_defi/data/vaults/formatted_logos` and are drawn on a dark
rounded tile, because many are white and would disappear in light newsletter
emails. The tiles are written to `podcasts/` in the bundle and uploaded to
Ghost with the charts. An episode post without a Spotify, YouTube or guest page
link logs a warning and is shown without it. The section is left out when the
Ghost Content API is not configured.

### Unidentified protocols

Generic ERC-4626 vaults, unknown and placeholder protocols form one "Other" pile
until their protocols are mapped, following the website's rule. Their data is
often broken, so they are counted only in TVL summaries (statistics, TVL by
protocol and by blockchain, inflows and outflows) and left out of every table and chart that
compares performance. See
[README-blog-post-outline.md](./README-blog-post-outline.md#unidentified-protocols).

### Tables

- Returns are annualised: (n) net of fees, (g) gross when fee data is not
  available. The export caps annualised returns at 10,000%, shown as `>9,999%`
  like on the website; ties are ranked by the absolute one-month return.
- Tables rank by the annualised one-month return, like the website. All charts
  use the steadier annualised three-month return instead, see
  [README-blog-post-outline.md](./README-blog-post-outline.md#common-rules).
- "3M price" shows the website's published 90-day sparkline (PNG, so it
  survives newsletter email clients). Low-TVL vaults may not have one yet.
- Tables have no risk rating column. The technical risk rating is used only to
  leave Dangerous or worse vaults out of the charts.

The thresholds live in `eth_defi.vault_report.sections.ReportCriteria`.

## Investability check

Some top-ranking vaults are not investable in practice: a Morpho vault lending
against a token with no market, or a pool whose depositors cannot withdraw.
Deciding this takes research outside the data we collect, such as block
explorers, DexScreener, protocol forums and X, so an LLM agent does it with the
[check-top-list-vaults skill](../../.claude/skills/check-top-list-vaults/SKILL.md).
The design and its trade-offs are in
[the plan](../../.claude/plans/2026-09-26-vault-report-investability-check.md).

Version 1 covers:

| Protocol | Checks |
|---|---|
| Morpho | suspicious collateral or positions; no exit liquidity |
| Euler | suspicious collateral or positions; no exit liquidity |
| 40acres | no exit liquidity |

The check will be extended to other protocols by adding a row to
`vault_checks.CHECK_SCOPE`, a probe to `vault_probes.py` and a section to the
skill. Vaults of other protocols pass through unchecked.

### How it runs

`eth_defi.vault_report.vault_checks.run_vault_checks()` runs before the report
is rendered:

1. It collects every ranked list the report would publish (the tables, the
   performance charts, the per-chain chart and the hero image) with the real
   selectors, 50% deeper than they are shown, so excluded vaults can be
   replaced. The largest vaults of the average yield charts are prescreened
   too.
2. `vault_probes.fetch_candidate_facts()` reads the in-scope vaults onchain:
   Morpho withdraw-queue markets and their collateral, Euler Earn strategies
   and EVK collateral, 40acres free liquidity, DEX liquidity of the collateral
   from DexScreener, and the last 30 days of liquidity from the price Parquet.
   It raises deterministic signals, which are triggers for research, not
   verdicts.
3. The agent CLI runs unattended with the skill, the candidates and the facts,
   and writes a decisions file: `exclude`, `keep` or `uncertain` for every
   candidate, with evidence. The pipeline validates the file and aborts on a
   missing, stale or malformed one.
4. If exclusions make the lists shorter than shown, the next vaults are
   checked in another round, up to three rounds.
5. Excluded vaults leave every ranking, chart, the T-bill caption and the hero
   image, and the inflows and outflows, and are listed in the *Excluded vaults
   in this report* section. They stay in the TVL totals, which report where
   money is, not where to invest. `uncertain` vaults stay in the report with an
   editor callout.

The check files go to the report bundle: `vault-check-candidates-N.json`,
`vault-check-facts-N.json`, `vault-check-decisions-N.json` and the agent
transcript `vault-check-agent-N.jsonl`. A rerun on the same data reuses the
decisions instead of running the agent again. Each decisions file is tied to
its candidate lists by a digest, so a rerun after the downloads have refreshed,
six hours later, usually needs the agent again.

The agent runs without a sandbox, because it needs web search, X and the
repository's RPC scripts. It is told to only read, and to write only the
decisions file and `eth_defi/vault/flag.py`. The September 2026 runs took
26–32 minutes over three rounds with the Claude CLI.

The agent is not deterministic. Two runs on 2026-09-26, on data a few hours
apart, agreed on the clear cases (King RSS, the 40acres pools, the Stream and
Elixir bad-debt pools) but not on every borderline vault: a vault lending
against its curator's own token was excluded by one run and left `uncertain`
by the other. Check the `uncertain` and borderline decisions each month.

### Agent CLIs

The Claude CLI, the default:

```shell
claude -p "<prompt>" --permission-mode dontAsk \
    --allowedTools "Bash,Read,Write,Edit,Grep,Glob,WebSearch,WebFetch" \
    --output-format stream-json --verbose --no-session-persistence
```

The Codex CLI:

```shell
codex --search exec --json --ephemeral --sandbox danger-full-access [-m gpt-6-sol] "<prompt>"
```

The Claude CLI bills `ANTHROPIC_API_KEY` when it is set, ahead of the
claude.ai login. If `.local-test.env` exports an API key without credit, the
agent stops with "Credit balance is too low": run the report with
`unset ANTHROPIC_API_KEY` after sourcing the environment to use the login.

The Claude CLI in print mode stops waiting for the agent's background
sub-agents after 600 seconds and exits, even when the decisions are not yet
written. The runner sets `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` so it waits
for them; the round timeout still bounds the run.

`vault_checks.build_agent_command()` builds both commands, with stdin closed
and the JSONL stream written to the transcript. Read
`.claude/docs/agent-tricks-and-troubleshooting.md` before changing them.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_CHECK_AGENT` | `none` | `claude`, `codex`, `reuse` (only reuse saved decisions) or `none` (no check) |
| `VAULT_CHECK_MODEL` | CLI default | Model for the agent, e.g. `gpt-6-sol` for Codex |
| `VAULT_CHECK_DECISIONS` | | Comma-separated bundle directories whose decisions files can be reused |
| `VAULT_CHECK_OVERRIDES` | | JSON list of editor decision records that replace the agent's |
| `VAULT_CHECK_TIMEOUT` | `60` | Agent timeout per round, in minutes |
| `MAX_WORKERS` | `8` | Parallel onchain probes |

Without the check, the post gets an editor callout saying the top lists were
not checked.

To check the lists without rendering the report, or to probe a single vault:

```shell
source .local-test.env && VAULT_CHECK_AGENT=claude \
    poetry run python scripts/erc-4626/check-top-list-vaults.py

source .local-test.env && VAULT_ID=8453-0xf80c0529bd94c773844e459853cd91b9263dd525 PROTOCOL_SLUG=morpho \
    poetry run python scripts/erc-4626/probe-vault-positions.py
```

### Blacklisting

When the agent finds a likely scam, or a vault whose positions cannot be
valued or exited by construction, it adds a `VAULT_FLAGS_AND_NOTES` entry to
`eth_defi/vault/flag.py`, with confidence high. The skill suggests
`malicious`, `misleading_valuation`, `illiquid` or `controversial`; the pipeline
accepts any flag in `eth_defi.vault.flag.BAD_FLAGS`, e.g.
`depegged_denomination_token` for a vault denominated in a collapsed stablecoin.
That hides the vault on the website and in the data exports too. Merely illiquid
vaults, such as 40acres pools, are excluded from the report but not
blacklisted.

The agent never commits. The script prints the `flag.py` diff: review it,
then commit it in a pull request of its own.

### Review workflow

1. Read the *Excluded vaults in this report* table and the `uncertain`
   callouts in the draft.
2. Check the evidence of any surprising decision in
   `vault-check-decisions-N.json`.
3. To overrule the agent, write an overrides file and rerun on the same data
   with `VAULT_CHECK_AGENT=reuse`, `VAULT_CHECK_OVERRIDES` pointing to the file
   and, unless the rerun writes to the same bundle, `VAULT_CHECK_DECISIONS`
   pointing to the bundle with the decisions:

   ```json
   [{"vault_id": "8453-0x...", "decision": "keep", "reason": "Collateral has a primary-market NAV"}]
   ```

## Editor workflow

1. Run the script in the last week of the month.
2. Open the draft in Ghost Admin using the editor link the script prints.
3. Fill in the yellow `EDITOR:` callouts: the intro highlight, report content
   updates (changelog candidates are listed in the callout), community news and
   comments on the top vaults, the TVL trends by protocol and by blockchain and
   the largest inflows and outflows. Then delete the callouts.
4. Review the investability check, see [Review workflow](#review-workflow),
   and commit any `flag.py` blacklist entries separately.
5. Review the tables. Unusual entries, such as capped `>9,999%` returns or
   leveraged tokens, deserve a comment or a vault note.
6. Publish, then share the post. Attach `hero-square.png` when posting on X.

## Tests

```shell
source .local-test.env && poetry run pytest tests/vault_report
```

`tests/vault_report/test_vault_report.py` runs offline with synthetic data.
`tests/vault_report/test_vault_report_live.py` checks the real data sources (top
vaults JSON, Pro prices, FRED, chain logos, the latest podcast episodes) and
the Ghost APIs. Each test is
skipped when its credentials are missing. The Admin API test uploads a 1×1
pixel image, creates a draft and then deletes the draft. Ghost has no API to
delete uploaded images, so the test image stays in the media library.

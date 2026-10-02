# Monthly report tweets

Read back the edited [Ghost report](https://tradingstrategy.ai/blog), freeze its actual chart images, and review one standalone tweet per chart for `tradingprotocol`. Tweets run in blog order, at least 12 hours apart. The October example is in `tweet-plans/2026-10/tweet-plan.md`. It is a draft, including unresolved attribution; generation and PR previews do not publish tweets.

Install the existing dependencies plus the optional `posts` extra:

```bash
poetry install -E data -E test -E docs -E hypersync -E ccxt -E cloudflare_r2 -E duckdb -E posts
```

 Use Bash to source `~/local-test.env`, which supplies Ghost credentials and `TWITTER_CONSUMER_KEY`, `TWITTER_SECRET_KEY`, `TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_TOKEN_SECRET`. Secrets are inherited by Screen through the environment and never written into campaign files or command arguments.

## Prepare and edit

```bash
source ~/local-test.env
export GHOST_POST_SLUG=the-best-performing-stablecoin-vaults-october-2026
# Future report bundles include chart-metadata.json with exact displayed entities.
export CHART_EVIDENCE=/absolute/report-bundle/chart-metadata.json
poetry run python scripts/erc-4626/prepare-vault-report-tweets.py
```

The command prints a new revision's `tweet-plan.md`. Every revision contains a Ghost HTML snapshot, source JSON, chart evidence and frozen images. Re-running preserves old revisions. `PREVIOUS_PLAN=/absolute/previous/tweet-plan.md` carries wording forward only when chart bytes and evidence match. Changed charts return to draft.

For the committed October example, restore its ignored image files first:

```bash
export TWEET_PLAN="$PWD/eth_defi/vault_report/tweet-plans/2026-10/tweet-plan.md"
MODE=hydrate poetry run python scripts/erc-4626/prepare-vault-report-tweets.py
MODE=preview poetry run python scripts/erc-4626/prepare-vault-report-tweets.py
```

Read `github-comment.md` beside the plan. Edit the **fenced text** in `tweet-plan.md` by hand: these bytes are the outgoing body. Each `##` heading is internal, and the image's Markdown description is its outgoing alt text. Include the month, a short narrative, the canonical report link and the relevant winner tags. Only one image and one body are allowed per entry. The validator uses twitter-text weighted length, including URL and emoji rules.

Rankings use the actual chart metric: normally 3M annualised returns, 1M for new vaults, 3M Sharpe for its chart, TVL-weighted average yields for aggregates, latest plotted TVL/NAV for size and positive 30-day TVL increases for flows. Infrastructure protocols are not substituted for vault curators or fund managers. Take the first three distinct attributable organisations, retaining unresolved higher-ranked identities for review. `Other`, benchmarks and `Total` are never winners. Chain identities are keyed by numeric chain id in `chain-socials.json`; labels resolve through explicit aliases. Final report rendering records only visible series and points in `chart-metadata.json`, and upload binds those records to Ghost-served image bytes.

The October evidence was read from the final served chart labels. Adjacent tables and fresh dashboard data were not used to reconstruct rankings. Editorial images and unknown curator attribution retain explicit review issues. For the unlabelled October scatter, the original cached R2 metadata and original exclusion decisions reproduced all 617 visible points and the branded PNG byte for byte; its evidence records the source-data hash and matching served-image hash. Winners are ranked by 3M return among visible points, without claiming optimal risk-adjusted performance. Resolve these in `chart-evidence.json`, or set the entry to `status: excluded` with an `exclusion_reason`. Every source image remains represented in the plan. A named higher-ranked winner with a missing handle must not be silently replaced by a lower-ranked one.

Repository social metadata and aliases supply candidate handles. `MODE=verify-handles` uses the authenticated X user lookup and records numeric ids and a dated check. The official ownership source is also required. If lookup access is unavailable, an editor can instead record `verification_method: official_link`, `verified_at`, `official_source` and a substantive `verification_note` describing the actual primary-source account link. This explicit manual evidence permits a lookup-access fallback at dispatch; a successful lookup still enforces account presence and stored numeric ids. Winners lacking verified handles are named without tags. Add a `missing_handle` acknowledgement with a rationale naming the affected winners; the compiler verifies each appears by name in the body, preserves the exception in the frozen payload and dispatches no tag for that winner. The editor still manually approves wording and the exception. Missing handles without this acknowledgement block compilation. Hand-edited evidence must be reviewed alongside the regenerated preview.

For fewer than three distinct organisations, add a reviewed exception in entry YAML:

```yaml
acknowledged_exceptions:
- code: fewer_than_three
  rationale: The chart contains only GMX and YieldBasis native pools.
```

An explicit `selection_override` requires `entity_ids`, the same `metric` as evidence and a review `rationale`; ids must exist in the chart's bound entity evidence. It is used consistently in preview, validation and outgoing handles. The `top_three_vaults` acknowledgement can document a reviewed selection of three vaults sharing fewer than three organisations. Unknown fields and duplicate YAML keys fail closed.

## GitHub review and compilation

After authorisation to push review assets and comment on the PR:

```bash
export GITHUB_PR=1234 ASSETS_BRANCH=vault-tweet-review-assets
MODE=publish-preview poetry run python scripts/erc-4626/prepare-vault-report-tweets.py
```

A dedicated orphan asset branch contains the exact attachments. Every image link uses an immutable commit and is downloaded and hash-checked before posting. Tweet bodies are fenced so X handles do not notify unrelated GitHub users. The comment id is saved locally, allowing an explicitly requested re-run to update that same review comment. The derived comment is never used as the runner input.

After manual wording, winner and image review, set each included entry's YAML to `status: approved`, resolve evidence issues, and choose an explicit future UTC `start_at` in the front matter. Keep `interval_hours: 12`. Excluded entries do not consume time slots.

```bash
MODE=preview poetry run python scripts/erc-4626/prepare-vault-report-tweets.py
MODE=compile poetry run python scripts/erc-4626/prepare-vault-report-tweets.py
```

Compilation fails if any included entry is unapproved, invalid or unresolved. It writes `approved-campaign-<digest>.json`, binding exact text, alt text, image hashes, winner evidence, source snapshot and schedule. Later Markdown edits create a new immutable compilation; they never alter a running campaign. Re-review changed wording/evidence and copy the new digest explicitly when launching.

## Start, pause and resume

Keep `STATE_DIR` outside temporary directories and Git worktrees. Its SQLite database, account lock, logs and backup are account-wide across every campaign revision. Losing it can cause duplicate posts: restore state rather than bootstrap again. Save campaign bundles and state together in server backups.

```bash
source ~/local-test.env
export CAMPAIGN_FILE=/absolute/bundle/approved-campaign-REVIEWED_DIGEST.json
export APPROVED_CAMPAIGN_DIGEST=REVIEWED_DIGEST
export STATE_DIR="$HOME/.tradingstrategy/vault-report-tweets/state/tradingprotocol"
MODE=dry-run poetry run python scripts/erc-4626/run-vault-report-tweets.py
# First use only: inspect https://x.com/TradingProtocol for earlier campaign posts.
PROFILE_CHECK_REASON='Inspected account and campaign history; no previous campaign sends' \
  MODE=bootstrap poetry run python scripts/erc-4626/run-vault-report-tweets.py
# First real intended tweet is a supervised integration check, after review.
MAX_SENDS=1 poetry run python scripts/erc-4626/start-vault-report-tweets.py
```

A fresh campaign needs its first slot at least ten minutes after preflight. The launcher waits for authenticated runner readiness and verifies the digest before reporting success. Check the printed Screen log and the single intended image tweet, including alt text and account, then resume without `MAX_SENDS` for the remaining queue:

```bash
poetry run python scripts/erc-4626/start-vault-report-tweets.py
MODE=status poetry run python scripts/erc-4626/run-vault-report-tweets.py
screen -ls
screen -r vault-report-tweets-POST_ID
```

Detach with `Ctrl-a d`. Pause the attached runner with `Ctrl-c`; start the same compiled file and state directory to resume. Logs heartbeat at least once a minute while waiting and record returned tweet URLs. Screen survives SSH disconnects, but not a server reboot; resume explicitly after checking state.

Before each dispatch, the runner checks publication/public URL, current Ghost chart/context digests, all attachment bytes and winner accounts. Changes stop the queue for a new review; nothing is silently rewritten. Failed media upload/alt text never creates a text-only tweet. Uploads happen near dispatch and must still be unexpired. Confirmed sends are committed immediately and are skipped on every restart, including revised campaigns. Overdue slots move forward relative to the last actual account send, preventing catch-up bursts.

Known HTTP 429 rejections defer until reset with bounded observable waits. Post timeouts, connection loss, duplicate errors or 5xx responses stop as `needs_reconciliation`; the create request is never retried automatically. An interrupted `sending` row is also ambiguous. All campaigns on this account stop until an operator resolves it.

```bash
MODE=reconcile poetry run python scripts/erc-4626/run-vault-report-tweets.py
# After checking the actual tweet, including media and time:
MODE=reconcile ENTRY_ID='POST_ID:CHART_KEY' RESOLUTION=sent \
  TWEET_ID=ACTUAL_NUMERIC_ID SENT_AT=2026-10-02T12:00:00Z \
  RECONCILIATION_REASON='Verified this exact chart/body on the public account' \
  poetry run python scripts/erc-4626/run-vault-report-tweets.py
```

Alternatively use `RESOLUTION=not_sent` and substantive `RECONCILIATION_REASON` only after verifying there was no successful write; an empty timeline response is not proof. Decisions and bootstrap reasons are durably recorded. Intentional repeats of an existing post/chart identity are unsupported; create a separately designed, reviewed repeat workflow rather than deleting sent rows.

## Provider checks and current limitations

Read-only integration checks run without posting:

```bash
source .local-test.env && timeout 180 poetry run pytest tests/vault_report/test_tweet_campaign_live.py -q
```

A manual media capability check (uploads a chart and sets alt text; creates no tweet) is available after reviewing provider access:

```bash
source ~/local-test.env
export CHECK_CHART=/absolute/frozen/chart.png
poetry run python scripts/erc-4626/check-vault-tweet-api.py
```

On 2026-10-01, supplied OAuth credentials passed `/2/users/me` as TradingProtocol, numeric account `1451590808466071564`. `/2/media/upload` succeeded. `/2/media/metadata` and `/2/users/by` returned HTTP 402. No tweet was created. Do not repeatedly retry these rejections: resolve the application's credits/access with X before the supervised first-send check. Full create-with-image verification remains an operator step after campaign approval. The client uses the [X media metadata endpoint](https://docs.x.com/x-api/media/create-media-metadata) and [post creation API](https://docs.x.com/x-api/posts/manage-tweets/introduction).

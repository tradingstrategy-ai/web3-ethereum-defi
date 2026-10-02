# Scheduled tweets for the monthly vault report

Written 2026-10-01. Implementation plan; the scripts below are proposed interfaces and do not exist yet.

## Outcome

Turn the final Trading Strategy Ghost report into a manually editable campaign for **@tradingprotocol**: one standalone tweet with one chart every **12 hours**, in blog order. Each tweet has a short narrative, the report month and the relevant top-three winner tags. The editor reviews the same wording and images locally in Markdown and in a GitHub comment, edits the Markdown, then explicitly launches a script in GNU Screen on this server.

This task produces the implementation plan and its Claude review. Generating an actual campaign, publishing GitHub comments or assets, and starting tweets are subsequent implementation/operator steps.

## Earlier work and lessons learnt

- [PR #1600](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1600) added the Ghost draft pipeline, branded charts, investability filtering and manual editorial workflow. Its [skeleton review comment](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1600#issuecomment-5846041620) shows how reviewers see charts alongside Markdown.
- [PR #1615](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1615) documented the report code and its data sources.
- [PR #1616](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1616) switched to fresh private production R2 inputs, preserved edited Ghost templates and read back the October report with 17 charts and nine tables. Its editorial source is the [October 2026 report](https://tradingstrategy.ai/blog/the-best-performing-stablecoin-vaults-october-2026).

Read `.claude/skills/monthly-vault-report/SKILL.md`, `eth_defi/vault_report/README-blog-post-outline.md`, `README-best-vaults-news.md` and `README-vault-report.md` before implementation. Preserve the existing blog workflow and its rules. This campaign reads Ghost; it does not regenerate or overwrite the report.

The existing `GhostAdminClient.fetch_post_by_slug()` already returns HTML and status. `GhostContentClient` reads published posts. `report.json` records chart paths and section counts, **but currently has no exact ranked chart entities**. The associated tables use annualised one-month returns; most charts select by three-month returns, and chart filters can remove vaults that remain in tables. Taking the first three table rows would therefore tag the wrong winners.

Curator and protocol social metadata already live under `eth_defi/data/feeds/curators/` and `eth_defi/data/feeds/protocols/`. Resolve `canonical-feeder-id` aliases through `eth_defi.feed.sources`, including aliases to stablecoin issuers. `eth_defi.vault.curator` documents this inheritance. `eth_defi.feed.twitter_api` already uses Tweepy for collection and list management; its read retry policy must not be copied blindly to post creation.

## 1. Read the edited post back from Ghost

1. Require an explicit `GHOST_POST_SLUG` (or add an explicit post-id selector); never choose a moving “latest” post when starting a campaign. Derive the report month from the report title/metadata, not the download date.
2. Use the configured Ghost Admin API URL and credentials. Read `formats=html` through the existing Admin client to prepare a campaign from a draft, scheduled or published report. Return `GhostPost` consistently and keep network helpers named `fetch_*`. The [Ghost Admin posts reference](https://docs.ghost.org/admin-api/posts/overview) describes the post read interface.
3. Save a source snapshot: post id, slug, title, canonical public URL, status, publication and update timestamps, fetched-at UTC, HTML and a content digest. Keep the public URL separate from the Ghost editor/API URL; add it to the post model if required. Never include credentials or private API URLs with key parameters in artefacts or logs.
4. Parse the HTML DOM, associating each report chart with its enclosing headings, caption, introductory text and table. Enumerate **charts actually embedded in the edited post**, in document order. Ignore logos, podcast thumbnails, table sparklines and the feature/hero image unless the editor explicitly adds it as a campaign chart. Record every ignored image with a reason. Do not rely solely on filenames: an editor may replace, add or remove a chart.
5. Download and freeze those exact chart bytes locally, retaining the original URL, SHA-256, dimensions, MIME type and source heading. Use the post's canonical served image outside responsive thumbnail paths; do not guess Ghost `_o` original filenames. Ghost may optimise uploaded bytes: record both the pre-upload original hash and the downloaded served-image hash, bound by the exact upload-response URL. The served version is the default reviewed attachment. An original supplied by the editor is an explicit image override requiring its own preview and approval. Limit image downloads to configured trusted HTTPS media hosts, check redirects, use timeouts and reject corrupt files. Log progress with `tqdm_loggable.auto`.
6. Missing or ambiguous chart classification or winner evidence is an explicit review issue. Every included chart must have one tweet entry; unresolved charts remain in the draft plan and prevent live launch until resolved. Re-running generation writes a new revision directory, leaving old Markdown untouched. Carry forward edited entries by stable id only when their chart hash and evidence are unchanged; changed charts return to draft with the previous wording shown as a suggestion, never automatically approved or merged.

Draft preparation is allowed, but live launch requires the same post to be published and its public URL to work. At launch and before each send, refetch it: changed chart bytes/set, canonical URL, publication status or winner-related captions/tables pause the campaign. Compare normalised DOM content rather than raw HTML or `updated_at` alone, so a harmless Ghost editor re-save is accepted. Other prose edits produce a warning. Keep the full snapshot digest for audit and a separate digest of these campaign-relevant fields for preflight. Never substitute new text or charts automatically. Log pauses prominently; the operator checks `MODE=status` at least daily during a campaign, as a detached Screen log is not an alert channel.

## 2. Identify the winners and their handles

Add `chart-metadata.json` to future report bundles, populated in `render_report_charts()` **from the exact final selections used to draw each chart**. Include chart key, original image hash, metric and period, display order, original numeric values, filters and stable entity ids: vault id, curator slug, protocol slug and chain id. Export the entities actually visible in a chart, not just its initial candidate set. At draft creation, associate the upload-response URL and downloaded served-image hash with this original/evidence record, accounting for Ghost optimisation. This extends a small JSON manifest, not the production price Parquet schema.

For an existing post such as October, default to a small editor-completed chart evidence file containing the displayed entities, rank, metric, value and attribution. Verify it against the downloaded chart during review. Original bundle evidence may be reused only with a provable original/upload-response/served-image binding and the original input snapshot. Never infer chart rankings from current dashboard data, the neighbouring one-month table or unattended OCR. An unmatched replacement or manually added chart uses this same manual evidence workflow.

Use these deterministic rules, showing the rank and metric evidence beside each tweet for review:

| Chart family | Entities to tag | Selection and narrative |
|---|---|---|
| Best vaults: new, lending, RWA, perp return, perp Sharpe, AMM, tokenised funds | Curators/managers | Walk the actual displayed ranking and take the first three distinct curator organisations, ordered by their best vault's rank. List the winning vault and its curator together. Most charts use 3M return; Sharpe uses 3M Sharpe. Preserve any chart-specific exception documented in its evidence. |
| Best vaults on each chain | Curators | Select three distinct curators from the displayed per-chain winners ordered by the chart's 3M return, and name their vaults and chains. Say “among the displayed chain winners”; do not imply a single chain won this chart. |
| Yield by protocol, high TVL / high yield | Protocols | Top three by displayed TVL-weighted average annualised 3M yield within that chart's eligible protocol universe. The high-TVL chart restricts the universe by TVL; do not use its vertical display order as a yield rank. |
| Yield by blockchain | Chains | Top three by displayed TVL-weighted average annualised 3M yield among the displayed chains. |
| Protocol TVL / chain TVL | Protocols / chains | Top three by the final displayed TVL observation. Say “largest by TVL”, not “highest yielding”. Exclude “Other” and benchmarks. |
| Tokenised fund NAV | Fund manager/issuer organisations | Top three named funds by final displayed NAV. Resolve their documented manager/issuer; never invent a curator or describe the NAV rank as yield. |
| Inflows and outflows by vault / blockchain | Curators / chains | Top three positive displayed 30-day TVL increases. Describe “TVL increases”; these include returns and are not measured net deposits. Explain large outflows separately if useful. If no positive increases exist, require an editor-selected alternative metric and rationale or an explicitly approved chart exclusion; never automatically celebrate or tag outflow leaders as winners. |
| Risk and return scatter | Curators | Use the top three distinct curators by 3M return among points actually inside the displayed axes, with their vault names in the tweet/evidence. Say “return leaders among the displayed vaults”; do not claim they are the safest or have the best risk-adjusted performance. If exact point attribution is unavailable, require manual evidence before approval. |

For vault charts, the first three distinct curators can represent vault ranks beyond three; show those ranks in the internal evidence and describe them accurately. If the editor prefers the top three vaults instead, support an explicit selection override with its rationale and show duplicate curator tags only once. Preserve the underlying top-three vault names even when they share a curator.

Normalise handles from bare names or `https://x.com/...` profiles, deduplicate case-insensitively and render one `@handle` per organisation. Curatorless vaults may use a protocol handle **only when existing metadata explicitly identifies a protocol-native curator**; an infrastructure protocol is not automatically the vault manager. Chain handles need a small reviewed registry keyed by chain id (including documented identifiers for non-EVM chains), with organisation name, official profile source and last verification date.

Resolve the selected winners before checking their handles. A missing handle, ambiguous attribution, inactive account or duplicate alias is shown for review, not replaced with a lower-ranked winner just to obtain three tags. Validate handle ownership through official project links and, where available, batched X user lookup. Cache verified user ids and usernames; recheck the selected handles before each send to detect renames or reassignment, respecting read rate limits. If user lookup is unavailable for this app, require documented official-link verification and manual preflight; record this limitation and its date. A transient lookup failure waits or pauses, never invents a successful verification.

Missing ranking/attribution evidence or a selected winner's missing/unverified handle is a **hard block**, not an acknowledged exception. The fewer-than-three exception applies only when the chart genuinely has fewer distinct attributable organisations and every selected organisation has a verified handle. Include those available winners and require a visible editor acknowledgement. Do not fabricate a third tag. Each body and its mentions receive manual approval; check the current X automation rules during the provider spike.

## 3. Generate the editable Markdown campaign

Create a revisioned bundle under `~/.tradingstrategy/vault-report-tweets/{post-id}/{revision}/`. `OUTPUT_ROOT` overrides only the parent directory; generation owns the post/revision subdirectories and prints the resulting `TWEET_PLAN` path:

- `source-post.json` and `source-post.html`: the read-back snapshot.
- `charts/*.png`: frozen **served image bytes** downloaded from the post; preserve another supported source format if applicable. Store editor-supplied originals in `overrides/<sha256>.<ext>` and prepared upload variants in `upload-variants/<sha256>.<ext>`, each explicitly bound to its chart evidence and separately previewed/approved.
- `chart-evidence.json`: exact rank, metric, attribution and handle evidence, including manual resolutions.
- `tweet-plan.md`: **the sole editable source for tweet wording, order and image selection**.
- `github-comment.md`: a derived rendering of that Markdown with remotely viewable image links.
- `approved-campaign-<digest>.json`: compiled, immutable payloads and schedule derived from the final Markdown; never hand-edit or overwrite these content-addressed files. The digest covers the source/evidence/assets and final payloads/schedule, excluding only the digest field itself.

The account is fixed to `tradingprotocol`; validate `interval_hours == 12`. An editor chooses `start_at` in UTC; included entry number `i` is scheduled at `start_at + i × 12 hours` (zero-based). Show the derived timestamps under each heading in the review rendering. Seventeen charts would mean 17 tweets, with the last scheduled eight days after the first; derive the real count from Ghost rather than hard-coding 17.

Use a documented restricted Markdown format. One level-two internal heading per tweet, a fenced YAML metadata block, a fenced plain-text body and one ordinary Markdown image immediately below it. The image alt text becomes the attachment alt text. Internal headings, metadata, evidence notes and fence markers are never tweeted. Parse this format structurally; reject unknown or duplicate fields and extra body/image blocks. Define the complete schema: campaign fields shown below, and entry fields `id`, `chart_key`, `winner_evidence`, `status` (`draft`, `approved`, `excluded`), optional `exclusion_reason`, optional `selection_override` (entity ids, metric and rationale), and `acknowledged_exceptions` (typed records with code and rationale). Allowed exceptions are only a genuine `fewer_than_three` case or explicit `top_three_vaults` selection with duplicate curator tags deduplicated. Free-text internal notes never count as acknowledgement. Overrides must point to verified entities/metrics in the matching evidence and retain required tags. Excluded entries retain their heading/image and need a non-empty reason; the review checklist makes their omission explicit.

Illustrative syntax only; placeholders are deliberately invalid for live launch:

````markdown
---
schema_version: 1
campaign_id: "<ghost-post-id>"
account: tradingprotocol
report_month: "2026-10"
source_digest: "<source-snapshot-sha256>"
start_at: "<editor-selected-UTC-timestamp>"
interval_hours: 12
---

# October 2026 vault report tweets

## Lending vaults

```yaml
id: "<ghost-post-id>:lending_performance"
chart_key: lending_performance
winner_evidence: "chart-evidence.json#lending_performance"
status: draft
```

```text
October's lending chart highlights <vault A> from @<curator A>, <vault B> from @<curator B> and <vault C> from @<curator C>, ranked by annualised 3M return. Full report: <canonical public blog URL>
```

![October lending vaults: annualised returns and 90-day performance](charts/lending_performance.png)

Internal review note: confirm all three names, handles and ranking evidence.
````

The editor edits the `text` block directly, changes alt text and can reorder headings. The compiler preserves the body and its internal line breaks exactly, stripping only the fenced block's structural trailing newline. It never adds hidden hashtags, tags, a heading or a URL. After reordering, validation regenerates the shown schedule without rewriting the source text. Changed image selection must resolve to a frozen asset and matching evidence; forbid paths outside the bundle. Derive and check `source_digest` against the saved source artefacts; editing that front-matter value cannot change the trusted source. Missing required mentions, including one removed during a wording edit, are validation errors.

Each body should fit an ordinary 280-character tweet: one short narrative sentence naming the month and the relevant winners, plus the blog link. Use X-compatible weighted text counting, including Unicode/emoji and t.co URL handling; plain `len()` is insufficient. Pick and test a maintained implementation against official twitter-text fixtures before adding a dependency. Show the count in the review rendering. Overlong tweets, placeholder text, missing required winner mentions or misleading period labels fail validation; shorten them by hand instead of truncating or switching to long posts. UK English, sentence case headings and “onchain” throughout.

Keep separate tweet ids stable across wording changes and reordering. A replacement image retains its logical chart key; assign manually added charts persistent editor-reviewed keys that survive later fetches, independent of document order. Refuse removal or duplication of an included chart without an explicit reviewed exclusion. Show exclusions and exceptional tag counts in a campaign checklist. Nothing writes over an existing `tweet-plan.md`.

## 4. Make the GitHub preview show the same tweets and images

Render `github-comment.md` from the edited Markdown, showing each internal subheading, UTC time, exact body, winner evidence, weighted character count and **inline chart image**. Keep each body in a fenced `text` block so X handles cannot notify unrelated GitHub accounts; use profile links without bare `@` mentions in evidence notes. Exact body parity applies inside these fences. Include the source post link, campaign revision and Markdown digest; include the approved digest only when a matching compiled campaign exists. Enforce GitHub's current comment-size limit with an actionable error; a normal 17-chart campaign should fit one comment.

Relative local paths cannot display images in a GitHub comment. Prefer already-public Ghost chart URLs only after checking an unauthenticated download returns the reviewed bytes. If those URLs are private, mutable or unavailable, publish frozen chart assets to an orphan assets branch using the established `README-best-vaults-news.md` workflow. Use raw image URLs pinned to an immutable **commit**, not a moving branch. Keep chart binaries off the feature branch. Verify HTTP 200, MIME type and SHA-256 for every remote image before posting the comment. The local and GitHub renderings must have identical body text and image hashes.

Separate rendering from external writes: `MODE=preview` validates draft or approved Markdown and writes only derived preview files; it never compiles an approved campaign or publishes externally. `MODE=publish-preview` requires an explicit `GITHUB_PR` and user authorisation to push assets/comment; do not infer a PR or push on every generation. Use local `gh`, `--body-file` or structured API arguments, update only recorded campaign comment ids, and retain previous revision digests. GitHub wording edits are suggestions to copy back into the local Markdown. The comment is never an alternative source for the runner.

## 5. Compile and manually approve a fixed revision

The editor marks each finished entry `status: approved`, resolves all evidence and handle issues, chooses a future UTC start and regenerates the preview. This makes the required manual review visible without preventing direct Markdown edits.

`MODE=compile` is a separate explicit action: it fails unless every included entry is approved and every exclusion/exception is valid, then produces `approved-campaign-<digest>.json` with exact bodies, mention/user-id mapping, image bytes' hashes, alt text and schedule. It never produces an approved file from drafts or overwrites an earlier compilation. Recompiling edited Markdown creates a different content-addressed file, even within the same source revision directory, and prints its absolute path and digest. `MODE=preview` calculates the current input/payload digest and references a compiled file only when that exact digest matches. The final sequence is hand-edit → draft preview → mark approved → compile → final preview/comment review → launch. The user launches this specific file with `APPROVED_CAMPAIGN_DIGEST`; the launcher checks it matches. Any later edit requires a new compile, preview and reviewed digest. The running process reads frozen approved payloads and never incorporates live Markdown edits.

A fresh campaign must start at least ten minutes after launcher preflight completes; otherwise refuse launch and require a new start/compile/review. Past start times are accepted only for a verified resume with existing ledger entries. This prevents a delayed review from accidentally causing an immediate first post.

Before the first send, authenticate as the configured user and require username `tradingprotocol`; store and require the same numeric account id on restarts. Refetch Ghost and verify publication, URL, relevant source content and chart bytes; validate all payload lengths, attachments, tags and source paths. Preparation and dry-run modes never upload media or create tweets.

## 6. Posting API and credentials

Source credentials from **`~/local-test.env`**, as supplied by the user, without printing or editing that file. Reuse the repository names `TWITTER_CONSUMER_KEY`, `TWITTER_SECRET_KEY`, `TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_TOKEN_SECRET`; `TWITTER_BEARER_TOKEN` alone is not a user write credential. Check presence without values during implementation. Confirm the app has read/write permissions and the user token belongs to @tradingprotocol. If existing credentials lack write access, report that precise prerequisite; never silently fall back to another account.

Use Tweepy where it supports the required endpoints. [X's manage-post documentation](https://docs.x.com/x-api/posts/manage-tweets/introduction) accepts user OAuth tokens and describes attaching uploaded media ids to `POST /2/tweets`. Confirm endpoint/auth compatibility with the supplied credentials in the real integration check; keep the writer in a separate module from feed collection. OAuth 1.0a is the first choice for the repository's existing four credentials. If a current v2 media endpoint requires OAuth 2.0 user authentication, use a documented OAuth 1.0a-compatible upload endpoint or report the required OAuth 2.0 setup; do not treat an app bearer token as a substitute.

Upload the one chart **just before its scheduled tweet**, set its reviewed alt text and use the returned media id in post creation. [Media upload responses](https://docs.x.com/x-api/media/upload-media) include an expiry, so pre-uploading an eight-day campaign is unsuitable. Validate current image size/format limits and account API access during implementation. If the selected served image or explicit editor override is too large, create a readable upload variant during preparation, show that exact variant in both previews and reapprove its hash. Keep the served-source hash for Ghost preflight and a separate attachment hash for the selected upload file. No image transformations after approval. Tweet creation must include the image; a failed upload must never result in a text-only post.

Do not retry ambiguous post creation automatically. A timeout, connection loss, 5xx or crash after dispatch may mean X accepted the tweet. Persist the attempt before sending and enter `needs_reconciliation` if success cannot be established. Reads/media upload can use bounded retries; known no-write rate-limit responses may wait until the provider's reset time. Log retryable failures as concise warnings without tracebacks, and terminal failures as errors. Redact credentials, signed request headers and credential-bearing URLs.

## 7. Durable 12-hour runner in GNU Screen

Implement a synchronous, single-process runner. Use durable per-account state under `~/.tradingstrategy/vault-report-tweets/state/tradingprotocol/`, independent of the worktree and campaign revision. A transactional SQLite ledger is suitable for this tiny queue; do not involve production DuckDB caches or scanner state. Hold a Linux process lock for the account. A second launcher refuses while any campaign runner for that account is active; it does not enqueue a competing campaign. Subsequent campaigns retain the same account ledger and send-spacing rule.

Record campaign/post/chart ids, revision digest, reviewed payload hash, scheduled time, attempt id/time, state, returned tweet id/URL and confirmed send time. Record the outgoing request intent durably before dispatch and the returned id durably immediately after success. An interrupted `sending` entry becomes `needs_reconciliation` at restart. A unique `(account-id, ghost-post-id, chart-key, repeat-id)` identity prevents reruns or new wording revisions from reposting a chart already sent; `repeat-id` defaults to zero and only an explicit operator repeat operation may create another value. Never recover a missing ledger by assuming every tweet is unsent. Retain state backups and campaign execution receipts. Bootstrap an empty ledger only via an explicit first-use operation that checks a bounded recent timeline for this blog URL (or requires a recorded manual profile check when reads are unavailable). A missing ledger for an existing campaign requires restoration/reconciliation before launch.

Scheduling rules:

- First send at the chosen UTC start; subsequent scheduled slots are 12 hours apart.
- On restart, skip confirmed sent entries. If slots were missed, resume the oldest pending entry once eligible and space later sends **at least 12 hours after the previous confirmed send across campaigns**. Log the revised effective schedule; never burst overdue tweets.
- An unresolved attempt blocks later tweets for that account. Provide `MODE=reconcile` to look up a bounded recent timeline and show candidate tweet ids, exact text, media and time. Require explicit operator resolution to mark an attempt sent or confirm it was not sent; if timeline access is unavailable, use the public profile and record the operator's evidence. No automatic “not found, retry” inference.
- Known 401/403, invalid payload or unavailable required images stop sending with an actionable error; a duplicate-content response instead enters reconciliation. Respect 429 reset times and retain the approved queue; reuse an uploaded media id only while its recorded expiry permits it.
- Handle SIGINT/SIGTERM, preserve pending state, release the lock and print how to resume. Exit when the queue finishes. Emit a heartbeat at least every minute, the next tweet/time and each successful URL; bounded waits allow graceful stop.

Proposed environment-only interfaces, following repository script conventions:

```shell
# Prepare a new draft campaign from the actual edited Ghost post.
source ~/local-test.env && \
  MODE=generate \
  GHOST_POST_SLUG=the-best-performing-stablecoin-vaults-october-2026 \
  OUTPUT_ROOT="$HOME/.tradingstrategy/vault-report-tweets" \
  poetry run python scripts/erc-4626/prepare-vault-report-tweets.py

# After hand-editing tweet-plan.md, render a draft review (use the printed path).
source ~/local-test.env && \
  MODE=preview \
  TWEET_PLAN='<printed-absolute-path-to-tweet-plan.md>' \
  poetry run python scripts/erc-4626/prepare-vault-report-tweets.py

# After marking entries approved, compile, then preview the final digest.
source ~/local-test.env && \
  MODE=compile TWEET_PLAN='<printed-absolute-path-to-tweet-plan.md>' \
  poetry run python scripts/erc-4626/prepare-vault-report-tweets.py
source ~/local-test.env && \
  MODE=preview TWEET_PLAN='<printed-absolute-path-to-tweet-plan.md>' \
  poetry run python scripts/erc-4626/prepare-vault-report-tweets.py

# When explicitly authorised to publish the review comment/assets.
source ~/local-test.env && \
  MODE=publish-preview GITHUB_PR='<chosen-PR-number>' \
  TWEET_PLAN='<printed-absolute-path-to-tweet-plan.md>' \
  poetry run python scripts/erc-4626/prepare-vault-report-tweets.py

# Inspect or simulate an approved queue; replace MODE with dry-run/reconcile.
source ~/local-test.env && \
  MODE=status CAMPAIGN_FILE='<printed-absolute-path-to-approved-campaign-file>' \
  poetry run python scripts/erc-4626/run-vault-report-tweets.py

# After manual review, launch this exact compiled revision in a detached screen.
# Replace the two placeholders with the paths/digest printed by preparation.
source ~/local-test.env && \
  CAMPAIGN_FILE='<printed-absolute-path-to-approved-campaign-file>' \
  APPROVED_CAMPAIGN_DIGEST='<reviewed-digest>' \
  poetry run python scripts/erc-4626/start-vault-report-tweets.py
```

`start-vault-report-tweets.py` validates first, then starts `screen -dmS vault-report-tweets-<post-id>` with an absolute Poetry-environment Python path, absolute runner/campaign paths and environment credentials inherited without command-line secrets. It reports the actual session name, log path and initial schedule, checks screen startup and an authenticated runner readiness record, and fails visibly if the child exits. The runner waits for a durable launcher-ready flag before dispatch so a failed launch cannot post unexpectedly. Do not require a TTY or run the scheduler in a tool-call session.

Expose `MODE=dry-run`, `MODE=status`, `MODE=bootstrap` and `MODE=reconcile` on the runner. `dry-run` simulates the whole schedule without writes; `status` reports the ledger and next effective time. Bootstrap and reconciliation must hold the account lock with no sending runner active. `MAX_SENDS` is optional; `MAX_SENDS=1` exits after one confirmed send and defaults to draining the approved queue when unset. The launcher passes it through to the runner.

Pause by attaching with `screen -r <printed-session-name>` and sending Ctrl-C; resume by launching the same compiled file with the same state directory. To change **unsent** tweets, pause, edit their Markdown, validate/review, compile to a new digest-named file, review the final preview and launch the newly printed file/digest against the existing ledger. Sent entries remain immutable. Document that Screen survives SSH disconnects, not host reboots: after reboot, run the launcher again with the same ledger.

## 8. Implementation sequence and checks

1. **Non-public provider capability spike before substantial implementation:** check credential presence without values and authenticate as @tradingprotocol. Inspect app/token access level where available and tier/read availability (`users/me`, username lookup, timeline). With operator authorisation for this non-public write, upload a chart and set alt-text metadata without creating a tweet; verify the actual Tweepy endpoint/auth compatibility and expiry against the current docs. This proves only media-write access. Create-with-media access remains unproved until step 6; neither token presence nor identity lookup is proof. Report incompatible credentials/endpoints early. Inspect current X automation/mention rules; if the requested scheduled tagging cannot comply, report that conflict before implementation and obtain a revised requirement, rather than silently removing required tags.
2. **Source and evidence:** add DOM chart extraction and snapshots. Export final chart entity evidence in `report.py` and the image upload mapping. Implement manual evidence for existing/edited images. Check a read-back of the published October report and review its complete image inventory. Use an explicitly authorised real Ghost image upload to verify original/served hash binding; an unchanged editor re-save checks normalised digest stability.
3. **Editable campaign:** add `eth_defi/vault_report/tweet_plan.py` for typed campaign/entry models (`dataclass(slots=True)`), handle resolution, constrained Markdown parsing, validation and compilation. Add `prepare-vault-report-tweets.py` with environment-only modes. Test changed Ghost wording, added/removed charts, chart/table ranking differences, canonical aliases, duplicate/missing curators, fewer-than-three exceptions, Unicode/URLs, image tampering, hand edits and revision preservation.
4. **Review rendering:** produce local Markdown plus GitHub Markdown with identical fenced bodies/images, immutable assets and recorded comment ids. Verify remote image hashes, size limits and absence of bare mentions outside fences. When authorised to publish a preview, check the images actually render in the GitHub comment through its image proxy.
5. **Writer and runner:** add `eth_defi/vault_report/twitter.py`, `tweet_scheduler.py` and the run/start scripts. Use a fake clock and fake writer to test 12-hour spacing, stale fresh start refusal, missed slots, lock contention, cross-revision deduplication, media expiry/failure, 429, wrong account, source changes, interrupted sends and reconciliation. Test Screen startup/early exit and credential environment inheritance without real tweet writes; `screen` is installed on this host as of 2026-10-01.
6. **Single supervised first post after the implementation exists:** add credential-guarded focused tests/manual modes for identity, exact Ghost reads and a real chart download. Run the first intended campaign entry through the complete hand-review → compile → launcher → runner path with an explicitly bootstrapped ledger and `MAX_SENDS=1`. This one operator-authorised public send exercises upload, alt text, create and read-back end to end as @tradingprotocol; run outside normal CI, at the reviewed start time, and log its URL. Record it as slot one in the normal ledger, then resume the same compiled queue without `MAX_SENDS` for later entries at least 12 hours apart. If API read-back/timeline is unavailable, verify text/image/alt text on the public profile and document reconciliation as manual-only. Delete only if separately requested. Record exact commands, date, redacted account/environment and results in a PR comment when implementation has a PR, as required by AGENTS.md. Never claim the earlier media-only spike, a mocked writer or identity lookup is a successful real posting test.
7. **Operator docs:** add a Twitter workflow README, API documentation stubs and update the monthly report skill to point to it. Document credentials, editor review, preview publication, start/stop/resume, daily status checks, reconciliation, revisions and rate-limit behaviour. Reuse existing libraries; Tweepy is in the optional `posts` extra, so include `-E posts` with the repository's standard Poetry extras. Any new dependency needs a purpose comment in `pyproject.toml`.

Run only focused tests with `source .local-test.env && poetry run pytest ...`, obtaining the unedited worktree environment file from the main checkout if needed. Use at least a three-minute command timeout and run `poetry run ruff format` on changed Python files. Do not build Sphinx locally. No Python tests or Twitter API calls are needed for this plan-only change.

## Acceptance criteria

- The campaign comes from the final Ghost content and includes exactly one reviewed tweet and actual image for every included chart, with exclusions visible.
- Winner tags have auditable chart-specific ranking and attribution; month, period and metric agree with the image. At least three valid distinct winners are tagged wherever the chart supports them; exceptions require manual acknowledgement.
- Hand-edited Markdown produces the exact approved tweet bodies, alt text and image bytes. Both Markdown and GitHub previews show them, and edits invalidate the prior compiled digest.
- An explicitly launched Screen process posts only as @tradingprotocol, one image per tweet and no more often than every 12 hours, survives SSH disconnection and resumes safely from durable state.
- Ambiguous writes pause for reconciliation; crashes, overlapping sessions, revisions and missed schedule slots cannot silently cause duplicate or burst tweets.
- The real authenticated posting check is recorded separately from mocked/offline coverage before calling the integration ready.

## Plan review

Claude CLI reviewed the complete plan inline on 2026-10-01 with tools disabled, pinned model `claude-opus-5-5` (Opus 5.5), following `.claude/docs/agent-tricks-and-troubleshooting.md`. The successful initial result requested changes: fenced GitHub bodies, stale-start refusal, Ghost served-image binding, explicit preview/compile interfaces, defined exception fields and an early real-provider capability check. These are incorporated above, along with revision carry-forward, read-limit handling, single-campaign locking, ledger recovery and a supervised first real tweet instead of a public test post.

The second successful review confirmed those changes and identified three contradictions, now resolved: the early spike is media-only and the single public check follows the complete implementation; compilations use immutable digest-named files; and served-source versus approved attachment hashes are distinct.

**Final verdict: no blocking findings.** The third review returned a successful final result on 2026-10-01 and confirmed all three resolutions. These were document reviews of the complete inline plan, not code or live-integration reviews. No tweets, GitHub comments or assets were published during this planning task.

## Implementation status

Implemented 2026-10-01; see `eth_defi/vault_report/README-tweets.md` and the editable October campaign under `eth_defi/vault_report/tweet-plans/2026-10/`. The final published post now has 19 chart images, including two editorial additions. The editor requested that winners without a verified X handle be named without a tag, with an explicit `missing_handle` exception for manual review. Compilation enforces the name and records the reviewed exception. A real OAuth identity check succeeded; media upload succeeded but alt-text and user-lookup APIs returned HTTP 402. No tweet was created and the campaign stays draft pending manual review.

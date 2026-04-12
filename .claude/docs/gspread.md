# Google Sheets integration tests — setup notes

Integration tests under `tests/hyperliquid/test_vault_review_sync.py` (and any other
gspread-backed tests) need a service account and a dedicated spreadsheet.
This note captures the parts of the setup that cannot be reliably automated
with browser MCP tooling and must be handled by a human operator.

## TL;DR — in this Workspace environment, Claude cannot share sheets with a service account

**The "Share" step must be done by a human** in the `tradingstrategy.ai`
Google Workspace environment we've tested. The Claude-in-Chrome plugin
and every other browser-based approach we have tried fail for this
specific action against this Workspace configuration. Claude can
complete every other step of the Google Sheets test setup autonomously,
but when it reaches "share the spreadsheet with the service account as
Editor" it will stop and ask the operator to do it manually in their own
browser. Other Workspace orgs may behave differently — we haven't
verified.

## Required resources

- A dedicated Google Cloud project (e.g. `web3-ethereum-defi`) with the
  Google Sheets API enabled.
- A dedicated service account inside that project (e.g.
  `web3-ethereum-defi@web3-ethereum-defi.iam.gserviceaccount.com`).
- A JSON key for that service account, serialised as a single-line string
  into the `TEST_GS_SERVICE_ACCOUNT_JSON` environment variable (do not store
  the JSON file inside the repo).
- A dedicated spreadsheet and worksheet tab used only for test traffic,
  shared with the service account email as **Editor**.
- Environment variables exported in `env/local-test.env`:
  - `TEST_GS_SERVICE_ACCOUNT_JSON` — raw JSON string of the service account key
  - `TEST_GS_SHEET_URL` — full `https://docs.google.com/spreadsheets/d/.../edit` URL
  - `TEST_GS_WORKSHEET_NAME` — exact worksheet tab name

## Known sharing blocker via Claude-in-Chrome (documented 2026-04-11)

When Claude drives the Google Sheets Share dialog through the
Claude-in-Chrome plugin, the dialog either renders but immediately shows:

> Sorry, sharing is unavailable at this time. Please try again later.

…or the autocomplete dropdown for the service account email accepts a
click that doesn't register as a selection, and the dialog silently
collapses without granting access. Reloading the document, waiting
several minutes between retries, and re-opening the dialog from scratch
all produce the same outcome — so the failure is **not** transient.

The exact root cause is unclear. Candidate explanations:

- A Google Workspace organisation policy on `tradingstrategy.ai` that
  blocks sharing to external `*.iam.gserviceaccount.com` recipients from
  automation surfaces.
- Google's client-side share flow detecting the automation-driven
  interactions and rejecting them to defend against abuse.
- A timing mismatch between the Claude-in-Chrome click/keyboard events
  and the Sheets share dialog's internal state machine, which makes the
  "add recipient → set role → confirm" sequence never reach a valid
  submit state.

Calling the public Drive REST API (`POST
https://www.googleapis.com/drive/v3/files/{fileId}/permissions`) directly
from a browser `fetch()` in the same tab **also** fails: the session
cookies Google sends with `docs.google.com` do not constitute a valid
OAuth2 credential for `googleapis.com`, and the request returns
`401 UNAUTHENTICATED` / `CREDENTIALS_MISSING`. There is no usable
workaround from inside the browser session.

### Implication for Claude Code sessions

Claude Code cannot complete the "share with service account" step through
the Claude-in-Chrome plugin in this environment. The human operator must:

1. Open the target spreadsheet in their own browser session.
2. Click **Share**, add the service account email, set the role to **Editor**,
   and confirm.
3. Notify Claude that sharing has been completed so the remaining setup
   (worksheet tab rename, env var writes, test code updates) can continue.

Do not waste time retrying the Share dialog or attempting alternative
JavaScript-based hacks from inside the browser — both paths are already
known to fail for this workflow.

### What Claude can do end-to-end

Everything else in the setup pipeline is automatable and has been verified:

- Create the GCP project via `console.cloud.google.com`.
- Enable the Sheets API.
- Create the service account and download the JSON key (honouring the
  explicit-permission rule for file downloads).
- Create the spreadsheet and rename the document title via the Sheets UI.
- Rename the worksheet tab **after** the sheet has been manually shared
  with the service account. The simplest way is a one-off
  ``google-api-python-client`` ``spreadsheets.batchUpdate`` call with an
  ``updateSheetProperties`` request — ``google-auth`` and
  ``google-api-python-client`` are already in the project's
  transitive dependencies via the ``-E data`` extra, so no extra install
  is needed even before ``-E gsheets`` is available. ``gspread`` works
  equally well once the ``-E gsheets`` extra is installed.
- Serialise the service-account JSON into an env var, write it to
  `env/local-test.env`, and delete the downloaded `.json` file.
- Update the test module to read `TEST_GS_*` variables and construct
  credentials from `TEST_GS_SERVICE_ACCOUNT_JSON` instead of a file path.

## Operator checklist

When resuming this setup, follow this order:

1. Verify GCP project, Sheets API, service account, and JSON key exist.
2. Verify spreadsheet exists and you are the owner.
3. **Manually** share the spreadsheet with the service account email as
   Editor (this step is the blocker above).
4. Ask Claude to finish: rename the worksheet tab via gspread, write
   `TEST_GS_*` env vars into `env/local-test.env`, update the test code,
   and clean up the downloaded JSON key file.

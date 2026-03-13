# Hyperliquid vault data backfill from S3 archive

> **This pipeline does not work.** The `account_values/` prefix does not exist in the
> public `hyperliquid-archive` S3 bucket. It lives in a separate private bucket used
> internally by Hyperliquid for [stats.hyperliquid.xyz](https://stats.hyperliquid.xyz).
> The [hyperliquid-dex/hyperliquid-stats](https://github.com/hyperliquid-dex/hyperliquid-stats)
> repo references `account_values` as a data type, but reads from a private `bucket_name`
> configured in `config.json` — not from `hyperliquid-archive`. The public bucket only
> contains `asset_ctxs/` and `market_data/` prefixes, as confirmed by the
> [official documentation](https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data).
>
> Discovered 2026-03-12.

Research conducted 2026-03-11.


## Problem

The Hyperliquid API's `allTime` period provides only **weekly snapshots** for data older
than 30 days. Vaults created before our daily scan pipeline started have gaps of 5–7 days
between data points, creating jigsaw share price curves (e.g. NEET vault has 60 rows
but should have 112).

## Solution: `hyperliquid-archive` S3 bucket

The `s3://hyperliquid-archive/account_values/` prefix contains **daily snapshots** for every
address on Hyperliquid, including vaults. This provides the exact fields needed for share
price calculation at daily resolution, going back to the archive's start date.

### Schema of `account_values/{YYYYMMDD}.csv.lz4`

```
time, user, is_vault, account_value, cum_vlm, cum_ledger
```

| Field | Maps to our column | Description |
|-------|-------------------|-------------|
| `account_value` | `tvl` | Total vault NAV (= `cumulative_account_value`) |
| `cum_ledger` | — | Cumulative net deposits from inception |
| `account_value - cum_ledger` | `cumulative_pnl` | Derivable: cumulative trading PnL |
| `is_vault` | — | Boolean filter (`true` for vault addresses) |
| `cum_vlm` | — | Cumulative trading volume (bonus data) |

Also available: `ledger_updates/{YYYYMMDD}.csv.lz4` with per-address deposit/withdrawal
deltas (`time, user, delta_usd`) for exact flow event timing.

## Data size estimates

### `account_values` file sizes

| Period | Est. active addresses/day | Compressed (LZ4) | Uncompressed |
|--------|--------------------------|-------------------|--------------|
| Early 2023 | ~50,000 | ~1.5–2 MB | ~6 MB |
| Late 2024 | ~300,000 | ~9–12 MB | ~36 MB |
| Now (2026) | ~1,200,000 | ~36–48 MB | ~144 MB |

**Total archive (all days, ~800–900 files): ~8–15 GB compressed, ~30–60 GB uncompressed.**

### Vault-only subset

After filtering `is_vault=true`:
- ~1,500–5,000 vault rows per day (growing from ~100 early on to ~5,000 now)
- **Total vault-only data: ~40–150 MB uncompressed** — tiny

## Cost analysis

The bucket is **requester-pays** — requires AWS credentials.

### Processing options

| Option | Viable? | Cost | Notes |
|--------|---------|------|-------|
| **A: Download locally** | Yes | **~$0** | 8–15 GB fits within 100 GB/month free egress tier. Simplest approach |
| B: AWS Athena | No | — | LZ4 framing format incompatible with Athena. Would need format conversion first |
| **C: EC2 same-region** | Yes | **~$0.02** | Free intra-region S3 transfer. Process on t3.small, download vault-only results (~150 MB) |
| D: S3 Select | No | — | S3 Select does not support LZ4 compression (only GZIP/BZIP2) |

### Cost breakdown for Option A (download locally)

| Item | Cost |
|------|------|
| S3 GET requests (~900 files) | $0.00036 |
| Data transfer OUT (8–15 GB) | $0.00 (free tier) |
| **Total** | **~$0** |

Download time: ~10–25 minutes at typical broadband (50–100 Mbps).
LZ4 decompresses at ~800 MB/s — processing is I/O bound, not CPU bound.

### Cost breakdown for Option C (EC2 same-region)

| Item | Cost |
|------|------|
| EC2 t3.small (2 vCPU, 2 GB RAM, ~1 hour) | $0.02 |
| S3 transfer (same-region) | $0.00 |
| Download results (~150 MB) | $0.00 (free tier) |
| **Total** | **~$0.02** |

### Ongoing incremental cost

One new file per day (~30–50 MB compressed) — effectively free.

## Bucket access details

- **Bucket**: `s3://hyperliquid-archive/`
- **Region**: `eu-west-1`
- **Access**: Requester-pays. Requires AWS credentials + `--request-payer requester`
- **Format**: LZ4-compressed CSV (`{type}/{YYYYMMDD}.csv.lz4`)
- **Update frequency**: Approximately monthly

## Implementation

The backfill is implemented as a two-stage pipeline in `eth_defi.hyperliquid.backfill`:

### Stage 1: Extract (S3 → staging DuckDB)

Downloads LZ4 files, extracts vault-only rows (`is_vault=true`), stores in a staging
DuckDB (`s3-vault-backfill.duckdb`), deletes the LZ4 file. **Resumable** — on restart,
skips dates already in the staging DB.

**Script**: `scripts/hyperliquid/extract-s3-vault-data.py`

### Stage 2: Apply (staging DuckDB → main DuckDB)

Reads from staging DB, inserts missing dates into the main `daily-metrics.duckdb`,
recomputes share prices. Only fills gaps — never overwrites API data. Each row is tagged
with `data_source='s3_backfill'` to distinguish from API data.

**Script**: `scripts/hyperliquid/backfill-vault-data.py`

### What the backfill provides

From S3 data we derive:
- `tvl = account_value`
- `cumulative_pnl = account_value - cum_ledger`
- `daily_pnl = cumulative_pnl[i] - cumulative_pnl[i-1]`
- `share_price`, `daily_return`, `epoch_reset` via `recompute_vault_share_prices()`

Columns that will be NULL for backfilled rows (no downstream impact):
`follower_count`, `apr`, `is_closed`, `allow_deposits`, `leader_fraction`,
`leader_commission`, `daily_deposit/withdrawal_*`

## AWS setup

The `hyperliquid-archive` bucket is **requester-pays**, meaning you need your own
AWS account and credentials to download files. The bucket owner pays nothing for
your requests — all S3 GET and data transfer costs are billed to your account
(effectively $0 for this workload, see cost analysis above).

### 1. Create an AWS account

If you don't have one already, sign up at https://aws.amazon.com/.
The AWS Free Tier includes 100 GB/month of data transfer out, which covers
the full archive download (~8–15 GB).

### 2. Create an IAM user with S3 read access

In the AWS Console → IAM → Users → Create user:

- **User name**: e.g. `hyperliquid-archive-reader`
- **Permissions**: Attach the managed policy `AmazonS3ReadOnlyAccess`,
  or create a minimal inline policy:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::hyperliquid-archive",
                "arn:aws:s3:::hyperliquid-archive/*"
            ]
        }
    ]
}
```

### 3. Create access keys

IAM → Users → your user → Security credentials → Create access key.
Choose "Command Line Interface (CLI)" as the use case. Save the
**Access Key ID** and **Secret Access Key**.

### 4. Configure AWS credentials

First, install the AWS CLI and create a named profile with your long-term access keys:

```shell
# macOS: brew install awscli
# Linux: pip install awscli

aws configure --profile hyperliquid
# Enter your Access Key ID (AKIA...) and Secret Access Key when prompted
# Set region to eu-west-1 (where the hyperliquid-archive bucket is located)
```

This creates `~/.aws/credentials` with a `[hyperliquid]` section containing your keys.

### 4a. If MFA is enabled on your account

Some AWS accounts enforce MFA authentication via an identity-based policy (e.g.
`Manage_credentials_and_MFA_settings`). In this case, long-term access keys alone
are not sufficient — all S3 requests return `AccessDenied` even with valid credentials
and `--request-payer requester`.

**Signs that MFA is required:**

- `aws s3 ls s3://hyperliquid-archive/...` returns `AccessDenied`
- `aws iam list-attached-user-policies` returns an error mentioning
  `explicit deny in an identity-based policy`

You must obtain **temporary session credentials** via `sts:GetSessionToken` before
accessing the bucket. The `--otp` argument is the 6-digit one-time password from your
MFA authenticator app (e.g. Google Authenticator, Authy, 1Password — the same app
you used when enabling MFA on your AWS IAM user). Use the helper script:

```shell
scripts/hyperliquid/refresh-mfa-session.sh --otp 123456 --profile hyperliquid

# Or inject credentials into the current shell as environment variables
eval $(scripts/hyperliquid/refresh-mfa-session.sh --otp 123456 --profile hyperliquid --export)
```

The script auto-detects your MFA device ARN. You can also provide it explicitly:

```shell
scripts/hyperliquid/refresh-mfa-session.sh \
  --otp 123456 \
  --profile hyperliquid \
  --serial arn:aws:iam::123456789012:mfa/my-device \
  --duration 43200
```

Session tokens expire after 12 hours by default (`--duration 43200`). Re-run the
script with a fresh OTP code to renew.

### 4b. Set environment variables

After configuring credentials, set the environment variables for the scripts.

**Option A: use a named profile** (recommended with MFA):

```shell
export AWS_PROFILE=hyperliquid
```

**Option B: use explicit environment variables** (e.g. from `--export` mode above):

```shell
export AWS_ACCESS_KEY_ID=ASIA...    # Note: starts with ASIA, not AKIA
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
```

Add these to your `.local-test.env` or shell profile for persistence.

### 5. Verify access (optional)

If you also have the AWS CLI installed, you can verify:

```shell
# macOS: brew install awscli
# Linux: pip install awscli

aws s3 ls s3://hyperliquid-archive/account_values/ --request-payer requester | head -5
```

### 6. Install Python dependencies

```shell
poetry install -E hyperliquid_backfill
```

## How to run

### Step 1: Extract vault data (download + extract)

The extract script downloads files from S3 and extracts vault data in one step:

```shell
# Using a named profile (recommended)
AWS_PROFILE=hyperliquid LOG_LEVEL=info \
    poetry run python scripts/hyperliquid/extract-s3-vault-data.py

# Or using explicit environment variables
AWS_ACCESS_KEY_ID=ASIA... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=... LOG_LEVEL=info \
    poetry run python scripts/hyperliquid/extract-s3-vault-data.py
```

Files are cached in `~/hl-archive/account_values/` by default. On re-run, only
new files are downloaded and already-extracted dates are skipped.

To extract a specific date range:

```shell
AWS_PROFILE=hyperliquid \
START_DATE=2025-11-01 END_DATE=2026-01-31 DELETE_LZ4=false LOG_LEVEL=info \
    poetry run python scripts/hyperliquid/extract-s3-vault-data.py
```

Environment variables:
- `AWS_PROFILE` — named profile from `~/.aws/credentials` (recommended)
- `AWS_ACCESS_KEY_ID` — AWS access key ID for S3 download (alternative to `AWS_PROFILE`)
- `AWS_SECRET_ACCESS_KEY` — AWS secret access key for S3 download
- `AWS_SESSION_TOKEN` — AWS session token for MFA-authenticated access
- `S3_DATA_DIR` — directory with pre-downloaded `.csv.lz4` files (skips S3 download if set)
- `S3_DOWNLOAD_DIR` — where to cache downloaded files (default: `~/hl-archive/account_values/`)
- `STAGING_DB_PATH` — staging DB path (default: `~/.tradingstrategy/hyperliquid/s3-vault-backfill.duckdb`)
- `START_DATE`, `END_DATE` — optional date range filter (YYYY-MM-DD)
- `DELETE_LZ4` — delete LZ4 files after extraction (default: `true`)

#### Alternative: use pre-downloaded files

If you already downloaded the files with `aws s3 sync`:

```shell
S3_DATA_DIR=~/hl-archive/account_values/ LOG_LEVEL=info \
    poetry run python scripts/hyperliquid/extract-s3-vault-data.py
```

### Step 3: Apply backfill

```shell
LOG_LEVEL=info poetry run python scripts/hyperliquid/backfill-vault-data.py
```

Environment variables:
- `STAGING_DB_PATH` — staging DB path
- `DB_PATH` — main metrics DB path (default: standard location)
- `VAULT_ADDRESSES` — optional comma-separated filter
- `RUN_PIPELINE` — if `true`, run downstream cleaning pipeline

### Step 4: Optionally run downstream pipeline

```shell
RUN_PIPELINE=true poetry run python scripts/hyperliquid/backfill-vault-data.py
```

## What this fixes

| Before backfill | After backfill |
|-----------------|----------------|
| Weekly gaps (5–7 days) in early history | Daily data points for all vaults |
| 60 rows for NEET vault | ~112 rows (one per day) |
| Jigsaw share price curves | Smooth daily equity curves |
| ~52 missing days for vaults created before daily scan | All days filled from S3 archive |

## Limitations

- Archive is updated ~monthly, so the most recent ~0–30 days may not be in S3 yet.
  These are already covered by the daily scan pipeline's `month` period from the API.
- `account_values` provides one snapshot per day per address. We cannot get sub-daily
  resolution from this source (but `month`/`week`/`day` API periods cover recent data).
- The archive start date is uncertain — may not go back to the very earliest vaults
  (late 2023). Need to verify by listing the bucket.
- `cum_ledger` may count leader equity differently from the API's `pnl_history` — need to
  verify that `account_value - cum_ledger` matches our `cumulative_pnl` for known vaults.

## Local testing

### Unit tests (no AWS needed)

```shell
source .local-test.env && poetry run pytest tests/hyperliquid/test_backfill.py -v
```

Tests use synthetic LZ4 files and verify:
- LZ4 parsing and vault filtering
- Stage 1 extraction and resumability
- Stage 2 gap-filling and share price recomputation
- API data preservation (backfill never overwrites existing rows)

### Manual test with real S3 data

```shell
# Stage 1: Download + extract a single day into test staging DB
AWS_PROFILE=hyperliquid \
START_DATE=2026-03-01 END_DATE=2026-03-01 \
STAGING_DB_PATH=/tmp/staging.duckdb DELETE_LZ4=false \
    poetry run python scripts/hyperliquid/extract-s3-vault-data.py

# Stage 2: Apply to test metrics DB
STAGING_DB_PATH=/tmp/staging.duckdb DB_PATH=/tmp/test-metrics.duckdb \
VAULT_ADDRESSES=0x4cb5f4d145cd16460932bbb9b871bb6fd5db97e3 \
    poetry run python scripts/hyperliquid/backfill-vault-data.py

# Inspect results
poetry run python -c "
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from pathlib import Path
db = HyperliquidDailyMetricsDatabase(Path('/tmp/test-metrics.duckdb'))
df = db.get_vault_daily_prices('0x4cb5f4d145cd16460932bbb9b871bb6fd5db97e3')
print(f'Rows: {len(df)}')
print(df[['date', 'tvl', 'cumulative_pnl', 'share_price', 'data_source']].to_string())
db.close()
"
```

## Production data migration

```shell
# 1. Download + extract full S3 archive (~8-15 GB compressed, ~10-25 min download)
# Resumable — safe to interrupt and restart. Only downloads new files on re-run.
AWS_PROFILE=hyperliquid LOG_LEVEL=info \
    poetry run python scripts/hyperliquid/extract-s3-vault-data.py

# 2. Back up production database
cp ~/.tradingstrategy/hyperliquid/daily-metrics.duckdb \
   ~/.tradingstrategy/hyperliquid/daily-metrics.duckdb.bak

# 3. Apply backfill (inserts only missing dates, never overwrites API data)
LOG_LEVEL=info poetry run python scripts/hyperliquid/backfill-vault-data.py

# 4. Run downstream pipeline to regenerate cleaned output
RUN_PIPELINE=true poetry run python scripts/hyperliquid/backfill-vault-data.py

# 5. Verify a known vault improved
poetry run python -c "
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from pathlib import Path
db = HyperliquidDailyMetricsDatabase(
    Path.home() / '.tradingstrategy/hyperliquid/daily-metrics.duckdb')
neet = '0x4cb5f4d145cd16460932bbb9b871bb6fd5db97e3'
df = db.get_vault_daily_prices(neet)
print(f'NEET vault: {len(df)} rows (was 60, expected ~112)')
sources = df['data_source'].value_counts().to_dict()
print(f'Data sources: {sources}')
db.close()
"
```

## Equity curve reconstruction (separate pipeline)

The equity curve reconstruction script
(`scripts/hyperliquid/reconstruct-equity-curve.py`) builds PnL, account value,
and share price curves from the local trade history DuckDB
synced via `sync-trade-history.py`. This is separate from the daily metrics
pipeline and the S3 backfill described above.

**Database**: `~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb`
— 1.9M fills, 2.2M funding, 82.6k ledger entries for 610 accounts.

### How it works

1. **Fills** (from `userFillsByTime` API) provide per-trade `closedPnl` and `fee`
2. **Funding payments** (from `userFunding` API) provide per-hour funding deltas
3. **Ledger events** (from `userNonFundingLedgerUpdates` API) provide deposits,
   withdrawals, vault creates, and vault distributions

The reconstruction:
- Merges fills and funding into a cumulative **PnL curve** via `pd.concat` + `.cumsum()`
- Computes **account value** = cumulative net deposits + cumulative net PnL
- For vaults, computes an event-accurate **share price** using ERC-4626-style
  mint/burn mechanics from `compute_event_share_prices()`

### Fill data limitation

The Hyperliquid `userFillsByTime` API only returns the **10,000 most recent
fills** per account. For active vaults this may cover only a few weeks of
history, even though funding and ledger data go back to the vault's creation.

To avoid misleading curves, the reconstruction **clips all data to start from
the first available fill**. Earlier funding and ledger events are excluded from
the PnL and account value curves. The chart heading and CLI output show the
actual data start date and the reason for the limitation.

The vault share price computation is an exception: it uses the **full unclipped
ledger** (all deposits/withdrawals from inception) for accurate total supply
tracking, combined with fills and funding only from the fill data window.

#### Example: Growi HF vault (too many fills)

The Growi HF vault (`0x15be61...`) was created on 2025-07-01 but the
`userFillsByTime` API returns only 12,668 fills starting from 2026-02-05 —
a **219-day gap** where ~90,000 fills are missing. At ~352 fills/day, the
API covers only ~14% of the vault's actual fill history.

Without clipping, the equity curve would show 7+ months of funding and
ledger data with no corresponding fill PnL, producing misleading near-zero
PnL despite the vault holding $1.4M.

With clipping, the reconstruction starts from 2026-02-05 and accurately
shows $5,941 net PnL over the 5-week data window. The chart subtitle and
CLI output explain the data start date and reason.

#### Example: IKAGI vault (complete fill data)

The IKAGI vault (`0xe44bed760c2f1a03a03bd1b8911f025d96e6eb04`) was created
on 2024-08-27 and has 6,214 total fills — well within the 10K API limit.
The equity curve covers the vault's full lifetime with no data gaps.
Share price tracks from 1.0 to ~1.075 with no epoch resets.

```shell
# Vault with complete fill data (no clipping needed)
ADDRESS=0xe44bed760c2f1a03a03bd1b8911f025d96e6eb04 \
  poetry run python scripts/hyperliquid/reconstruct-equity-curve.py
```

#### Sync lookback window

The `sync-trade-history.py` script defaults to fetching the last **365 days**
of fills, funding, and ledger events on first sync. For vaults older than
1 year, pass a `start_time` to capture the full history including the
`vaultCreate` event (required for share price reconstruction):

```shell
# First sync for an old vault — extend lookback to cover creation date
ADDRESSES=0xe44bed760c2f1a03a03bd1b8911f025d96e6eb04 \
  LABELS=IKAGI \
  TRADE_HISTORY_DB_PATH=/tmp/test-history.duckdb \
  INTERACTIVE=false \
  poetry run python scripts/hyperliquid/sync-trade-history.py
```

Or programmatically:

```python
db.sync_account(session, addr, start_time=datetime.datetime(2024, 8, 1))
```

Without the `vaultCreate` event, the share price computation starts with
zero total supply and produces nonsensical values.

#### How to tell if a vault has complete data

If `data_start_at` in the CLI output is close to the vault's creation date
(visible from the first `vaultCreate` ledger event), then fill data is
complete. If there is a gap of days or months between vault creation and
`data_start_at`, fills are truncated by the API limit.

| Vault | Created | First fill | Gap | Fills | Status |
|-------|---------|------------|-----|-------|--------|
| Growi HF | 2025-07-01 | 2026-02-05 | 219 days | 12,668 | Truncated (~14% coverage) |
| IKAGI | 2024-08-27 | 2024-08-27 | 0 days | 6,214 | Complete (100% coverage) |

### Running

```shell
# Vault with complete fill data (IKAGI, <10K fills)
ADDRESS=0xe44bed760c2f1a03a03bd1b8911f025d96e6eb04 \
  poetry run python scripts/hyperliquid/reconstruct-equity-curve.py

# Vault with truncated fill data (Growi HF, >10K fills)
ADDRESS=0x15be61aef0ea4e4dc93c79b668f26b3f1be75a66 \
  poetry run python scripts/hyperliquid/reconstruct-equity-curve.py

# Trader example
ADDRESS=0x162cc7c861ebd0c06b3d72319201150482518185 \
  poetry run python scripts/hyperliquid/reconstruct-equity-curve.py

# Without opening browser
ADDRESS=0x15be61aef0ea4e4dc93c79b668f26b3f1be75a66 \
  NO_BROWSER=true \
  poetry run python scripts/hyperliquid/reconstruct-equity-curve.py
```

### Relationship to S3 backfill

The S3 backfill (described above) fills gaps in the **daily metrics** database
which provides daily-resolution share prices. The equity curve reconstruction
reads from the **trade history** database which provides event-level resolution
but is limited by the 10K fill API cap. These are complementary:

| Pipeline | Database | Resolution | History |
|----------|----------|------------|---------|
| Daily metrics + S3 backfill | `daily-metrics.duckdb` | Daily | Full (via S3) |
| Equity curve reconstruction | `trade-history.duckdb` | Per-event | Limited by 10K fill cap |

## Verification plan

1. Download one recent day's `account_values` file
2. Extract vault rows for 3–5 known vaults (NEET, HLP, pmalt)
3. Compare `account_value` against our stored `tvl` for the same date
4. Compare `account_value - cum_ledger` against our stored `cumulative_pnl`
5. If values match: proceed with full backfill
6. If values differ: investigate the mapping before full backfill

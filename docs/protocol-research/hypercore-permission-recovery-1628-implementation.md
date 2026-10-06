# Hypercore collector repair and local recovery results

Local recovery for [issue #1628](https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1628)
was performed on 2026-10-06. Only isolated copies were modified. Production
services, databases and published datasets have not been migrated or deployed.

## Evidence and restored prices

Sources were seven retained scanner snapshots dated 2026-09-29 through 2026-10-05,
per database, plus the 2026-10-05 private R2 raw price archive. The current
2026-10-06 files were copied separately as targets. Original source files, hashes,
manual backups, own migration backups and JSON reports remain in the operator's
recovery evidence directory. Cleaned Parquet was excluded from restoration.

| Database | Before | Missing rows restored | After |
|---|---:|---:|---:|
| Daily | 41,620 | 0 | 41,620 |
| High frequency | 1,493,720 | 213,648 | 1,707,368 |

Scanner backups supplied 212,249 missing HF keys; raw R2 Parquet supplied 1,399.
All original target keys, economics and write clocks were retained. Existing
values won the 19,669 matching-key conflicts; the audit also retains source
alternatives for newly restored keys, totalling 102,273 HF conflict-evidence rows.
Duplicate keys and price-table ART constraints are zero. A further backed-up apply
preserved every logical table's count and full-column fingerprint.

## Corrected permission policy

When an independent observation clock is absent, explicitly selected backups
supply flags using the original price timestamp, with `legacy_price_timestamp`
provenance. Known-corrupted target flags are excluded. Scanner keys suppress raw
inferred observations at the same key, including scanner nulls. Genuine
observations, including Unknown, take precedence. Inferred flags expire two days
from their original clock and cannot bridge an unrecovered gap. Raw-only flags
can already have been carried forwards: this approximation cannot authenticate
independent measurements. No inferred flags establish fresh capacity.

The sidecar contains **1,203,463 records**, including **456,805 inferred snapshots**
(13,430 daily and 443,375 HF). Native receipts and unknown boundaries retain their
own clocks. Missing recoverable flags remain Unknown. Original strict receipt-only
conclusions have been superseded by the operator's price-clock policy.

## DOEZOE and production Parquet

DOEZOE (`0xcae0d1558b70b92ee9fd0acb20cb639c8c28ae69`) recovered 335 HF keys,
increasing its scanner rows from 2,867 to 3,202. The merged candidate has 3,316
DOEZOE rows. Its 1,027 inferred snapshots include no authenticated API receipt.

At UTC midnight, corrected state is **Open on 13–21 September** and **Closed from
22 September**. The first retained disabled flag is at **21 September 11:48:00.756
UTC**, with original write time 11:48:02.468780. This is the first retained record,
not proof of the actual transition instant. The previous Unknown conclusion for
15–17 September was incorrect under the requested fallback-clock policy.

The actual unchanged Trading Strategy client/executor agrees throughout
13 September–6 October after old write clocks are restored. It still disagrees
on source state for 6 March–6 April, before its assumed-open cutoff. The
[full 365-day DOEZOE table](doezoe-deposit-status-2026-recovered.md) includes exact
clocks, provenance, client behaviour and unevaluated future dates.

The final Arrow candidate contains **22,708,232 rows and 42 columns**. All
**20,959,244 non-Hypercore rows** retain their original full-column fingerprint,
including old Monad rows and EVM NaNs/nulls. The full merge took 154 seconds.
The validated candidate is `vault-prices-1h-price-clock.parquet`; an earlier
failed candidate must not be deployed.

## Checks and handover

The final focused suite passed **121 tests**, with one opt-in R2 skip. Checks cover recovery, backup failure, idempotence, clock units and age,
source precedence, Unknown handling, threaded ingestion, export, manifest and
remote cadence. Authenticated private Cloudflare R2 round-trip and forced
multipart-copy checks passed manually on 2026-10-06, using isolated temporary
prefixes. Grounded Claude CLI Opus 5.5 review findings were addressed; original
review streams remain with the recovery evidence.

Use the [runbook](../README-hypercore-permission-recovery.md) to reserve the daily/HF
backup window, stop owners, take gated offhost snapshots and run fresh production
dry runs before applying. Local rehearsal files are not production replacements.
Manifest v2 remains disabled until the paired sidecar contract is supported by
serving, client and executor. Client original-clock and freshness behaviour is
tracked in [Trading Strategy #252](https://github.com/tradingstrategy-ai/trading-strategy/issues/252).

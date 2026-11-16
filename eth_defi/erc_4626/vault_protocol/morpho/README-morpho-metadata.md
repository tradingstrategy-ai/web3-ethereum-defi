# Morpho metadata requests

Both vault versions use `offchain_metadata.py` to fetch warnings and curator
names. Successful responses remain cached for 24 hours. Cache hits bypass the
HTTP session, including its cooldown check.

`session.py` creates worker-local Requests sessions with a shared quota of two
requests per second. It uses PyrateLimiter's `FileLockSQLiteBucket` with its
lock beside the SQLite database, rather than the library's default `/tmp`
lock. Processes and containers must mount the same state directory to share
the budget. Other machines using the same public IP remain outside this quota.

The [Morpho API rate limits](https://docs.morpho.org/api/#rate-limits-and-best-practices)
include a longer ten-minute window. A low aggregate rate is necessary even
when individual requests are fast. HTTP 429 records the provider's
`Retry-After` deadline, including a seven-day ban. Missing or invalid headers
default to ten minutes. New reads defer during that cooldown; workers do not
sleep for its duration. Already in-flight requests may finish. A shorter later
response cannot shorten the existing deadline.

Each lookup makes at most one HTTP attempt, with the existing 30-second
inactivity timeout and a 60-second quota queue budget. Requests are not
redirected or retried internally. Other transient failures retry on a later
scan. The factory exposes optional rate, timeout and state-directory overrides
for library callers; defaults require no additional environment variables.

## State and recovery

The default directory is `~/.tradingstrategy/morpho/`:

- `rate-limit.sqlite` and `rate-limit.sqlite.lock` coordinate requests.
- `provider-cooldown.json` contains a version and a naive UTC `retry_at`.
- `provider-cooldown.json.lock` protects atomic cooldown updates.

Scanner startup may clear the short-lived `ratelimit_*` request tables. It must
retain the JSON cooldown. A successful response never deletes another worker's
ban. If a ban cannot be saved, the current process still blocks its workers,
but logs that other processes cannot observe the unsaved deadline.

Malformed or unreadable state defers enrichment with a concise warning, without
a traceback. Fix directory permissions or restore a valid cooldown file from
backup. For corrupt JSON, preserve a copy and reconstruct the deadline from
the logged provider resume time. The schema is `{"version": 1, "retry_at":
"2026-11-19T12:00:00"}`; that timestamp is only a format example. Do not delete
or shorten a valid, unexpired provider ban. State reads are retried on later
lookups, so repaired storage does not require a process restart.

## Warning preservation

Scan rows carry the private, optional `_morpho_api_status` key. A transient
lookup retains previously stored Morpho warning data, flags, warning notes
and curator name. Onchain values and maintained descriptions still update.
A later successful clean response may clear obsolete warnings; definitive
not-found handling remains unchanged. Legacy pickle rows need no migration.
Maintained notes keep their normal priority; retained API warning notes are
rebuilt from the retained payload rather than copied from old maintained text.

A newly discovered vault with no prior API result has no warning data to
retain. Its lookup remains `transient_error`, and this change does not invent
a missing-vault or risk flag based on a network failure. Existing export
behaviour for that unavailable enrichment is unchanged.

The per-vault cache lock also covers quota queueing and HTTP. A competing
same-vault lookup can time out waiting for that lock and defer its enrichment;
existing stored warning data is retained in that case too.

## Checks

Run the focused unit and real public-provider checks after loading the private
test environment:

```shell
source .local-test.env && timeout 180 poetry run pytest tests/morpho/test_morpho_session.py tests/morpho/test_morpho_offchain_metadata.py tests/erc_4626/test_scan_features.py -q
source .local-test.env && timeout 180 poetry run pytest tests/morpho/test_morpho_session_live.py -q
```

The live check uses an isolated state/cache directory and a real indexed vault.
It needs no API token or RPC connection, does not deliberately trigger a rate
limit, and does not retry a failed request. Record the dated result in the PR
comment when publishing the integration.

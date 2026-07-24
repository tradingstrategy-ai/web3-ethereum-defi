# Hypersync scan tests — disabled on CI by default

## What and why

The Hypersync vault-discovery scan tests stream millions of blocks from the
[Hypersync](https://docs.envio.dev/docs/HyperSync/overview) indexing service.
They were the slowest tests on the CI critical path — 70–85 s each on a Beefy
runner (2026-07-24 CI durations report), dominating the wall-clock of the main
test suite while mostly exercising the external Hypersync service rather than
our own code.

Since 2026-07-24 they are **disabled on CI by default** and run locally and on
demand instead. The affected tests live in `tests/erc_4626/test_scan.py`:

- `test_4626_scan_hypersync` — scans early Base chain blocks 1–4,000,000
- `test_lead_scan_core_hypersync[auto]` / `[hypersync]` — incremental lead-scan
  core over Base blocks 2,000,000–2,500,000

The gate is the `skip_hypersync_scan_on_ci` marker in that module:

```python
skip_hypersync_scan_on_ci = pytest.mark.skipif(
    CI and not RUN_HYPERSYNC_TESTS,
    reason="...",
)
```

`CI` is set automatically by GitHub Actions; `RUN_HYPERSYNC_TESTS=true`
re-enables the tests. Apply the same marker to any new multi-minute Hypersync
scan test instead of letting it back onto the every-commit critical path.

## Running locally

Nothing changed — the tests run by default outside CI (the skip only triggers
when `CI=true`). You need `JSON_RPC_BASE` and `HYPERSYNC_API_KEY` in your test
environment:

```shell
source .local-test.env && poetry run pytest tests/erc_4626/test_scan.py -k "hypersync"
```

## Running on CI when needed

Run them on CI when touching the Hypersync discovery code
(`eth_defi/erc_4626/hypersync_discovery.py`, `eth_defi/hypersync/`,
`eth_defi/erc_4626/lead_scan_core.py`) or upgrading the `hypersync` dependency.

### Option 1: manual workflow dispatch (recommended)

The main test workflow accepts a `run_hypersync_tests` input:

```shell
gh workflow run test.yml --ref <your-branch> -f run_hypersync_tests=true
```

or via the GitHub UI: *Actions → Automated test suite → Run workflow* and tick
*Run Hypersync scan tests*.

### Option 2: repository variable (persistent)

Set the repository Actions variable `RUN_HYPERSYNC_TESTS` to `true`
(*Settings → Secrets and variables → Actions → Variables*) to run the scans on
every CI run until the variable is removed:

```shell
gh variable set RUN_HYPERSYNC_TESTS --body true
gh variable delete RUN_HYPERSYNC_TESTS   # switch off again
```

### Option 3: one-off in any workflow or shell

Export the environment variable next to the pytest invocation:

```shell
RUN_HYPERSYNC_TESTS=true poetry run pytest tests/erc_4626/test_scan.py -k "hypersync"
```

## Related

- `docs/README-test-suite-performance.md` — the wider test-suite performance
  plan this change belongs to.
- `tests/erc_4626/test_scan.py` also contains `@pytest.mark.slow` RPC-scan
  variants that run in the separate slow-test workflow.

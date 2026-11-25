# Repository instructions

## Agents

- Before invoking an external agent CLI (Claude, Codex, Grok, Kimi or an equivalent wrapper), read and follow [agent CLI instructions](.claude/docs/agent-tricks-and-troubleshooting.md) in the current session.
- When the user names a skill or the task matches a directory in [.claude/skills](.claude/skills), read its `SKILL.md` first. Use its scripts, templates and checklists. Open additional references as needed.
- After a feature, bug fix or maintained content change, run [clean-up-for-pr](.claude/skills/clean-up-for-pr/SKILL.md) once before review handoff. Delegated reviewers must not start another cleanup review.
- Use the [repository guide](.claude/docs/README.md) to find component READMEs, runbooks and research. If a web fetch returns HTTP 403, retry in Chrome when the browser integration is available.

## Writing and documentation

- Use British English, spell `onchain` as one word, and use sentence case for headings and chart titles.
- Apply the [description writing style guide](.claude/docs/description-writing-style.md) to public website and data text, including descriptions, notes and fees. Engineering documentation and PR descriptions follow the English rules above.
- Link a source page at its first relevant mention. Give each API module a Sphinx stub under `docs/source/api/` and include it in the [API contents](docs/source/api/index.rst). See the [Lagoon index](docs/source/api/lagoon/index.rst) for an example.
- Do not build Sphinx locally or edit generated `_autosummary*` files.

## Development and worktrees

- Use Python 3.14. Install dependencies with `poetry install -E data -E test -E docs -E hypersync -E ccxt -E cloudflare_r2 -E duckdb`. Run scripts with `poetry run python path/to/script.py`.
- In a worktree, use the parent Poetry environment unless changing dependencies. Check that `.venv` resolves to it and that this checkout's `.claude/docs` and `.claude/skills` are accessible.
- Copy the gitignored `.local-test.env` from the main checkout into the worktree. Use `git worktree list` to locate it. If it is missing from both, ask the user to prepare it before running tests. Never edit or commit it. Keep credentials out of commits, test commands and output.
- Format only changed Python files, including new ones, with `poetry run ruff format path/to/changed.py`. Explain each added or updated dependency in a `pyproject.toml` comment.

## Python and scripts

- Type all function arguments and return values. Use `HexAddress` for blockchain addresses, `eth_defi.types.Percent` for percentages, and `dataclass(slots=True)` for data records where appropriate. Use snake_case for string enum members and values.
- Use module-level imports where possible. Use `import datetime`, naive UTC datetimes and `eth_defi.compat.native_datetime_utc_now()`. Assertions may check invariants and configuration. Run Python without optimisation so these checks execute.
- Prefix network reads with `fetch_`. Prefer iterators for streamed results, `any()` or `all()` for membership predicates, and Pandas operations for tabular data.
- Use threads for concurrent reads. For batches of API calls, use `joblib.Parallel` with the threading backend unless otherwise specified. Accept `max_workers` and expose `MAX_WORKERS` in scripts.
- Write Sphinx docstrings with a one-line summary, rationale, mechanism, parameters and return value. Document units, ownership, side effects, failure conditions and DataFrame or Series columns and types where relevant. Link canonical API documentation for integrations.
- Write comments for maintainers who know Python but may be new to the protocol. Explain unfamiliar terms on first use and give the reason for non-obvious decisions. Use Sphinx function or method references to explain a non-obvious caller. Document dataclass and enum members with `#:` above the member. Omit an override's docstring if the inherited one suffices.
- Use a module logger from `logging.getLogger(__name__)` and pass format arguments separately. Catch specific exceptions and report failures. For retries, log a concise warning identifying the operation. Log an error with traceback on the final failure.
- Show progress at least once a minute so the operator can tell whether a long-running script or scan has stalled. Use `tqdm_loggable.auto` for loops.
- Configure scripts through environment variables. Add a command-line parser only when requested. Require credentials and user-supplied RPC URLs when needed. Default routine settings, document overrides, and validate inputs before sending transactions.
- Use [scan-vaults.py](scripts/erc-4626/scan-vaults.py) for a script example. Use `tabulate.tabulate()` for terminal tables, DataFrames for notebook tables and Plotly for charts.

## Tests

- Run only the relevant tests with `source .local-test.env && poetry run pytest <test-path-or-node-id>`. Allow at least 180 seconds. Add `--log-cli-level=info` for diagnostics.
- Use pytest functions and fixtures. Put shared helpers under `eth_defi/testing/`. Avoid test classes, stdout prints and imports from `tests`. Use `pytest.approx()` for float comparisons. Close DuckDB connections with a fixture or `finally`.
- Test repository behaviour with representative raw inputs and explicit expected results. Cover calculations, fees, NAV, filtering, pagination, resume, persistence, exports and permissions. Exercise public dispatch contracts when callers rely on them.
- Avoid adding tests that only confirm provider responses, network timing, dataclass mechanics, private imports, literal registry sizes or precomputed fixture arithmetic. An integration change alone does not require an authenticated smoke test or PR comment.
- Keep representative registry cases for distinct outcomes, chain-specific mappings, overrides and edge cases. Preserve unique assertions before removing duplicate tests. Use unique function names and remove unused fixtures. When reporting reductions, give the suite scope and collection environment and distinguish functions from parametrised cases. See [PR #1767](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1767) and [PR #1769](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1769).
- Before writing an Anvil mainnet-fork test, read the [shared fork module docstring](eth_defi/testing/anvil_fork_pool.py). Use the session pool, canonical chain block, matching xdist group and snapshot/revert isolation. Deploy expensive fixtures once per session. Assert exact outcomes at fixed blocks. On Monad, use current-state or state-relative assertions.
- Mark new multi-minute Hypersync scan tests with `skip_hypersync_scan_on_ci`. They still run locally. Follow [Hypersync CI instructions](docs/README-hypersync-tests.md) when changing discovery code.

### Flaky CI tests

- Read the full traceback, record the failing test and symptom, compare the code with `master`, and run the focused test locally. Fix reproducible failures. Move resource-heavy tests to the slow workflow when contention causes failures and install their dependencies there.
- Use `@flaky.flaky` only for a demonstrated intermittent failure. Immediately above it, record the first observation date, CI symptom and evidence of a passing run. Keep the test enabled.
- Use a dated `@pytest.mark.skipif(CI, reason=...)` only if the marked test still fails on at least two distinct CI runs, passes locally and the external cause cannot be fixed here. Describe the symptom in the comment and reason. Keep local coverage. Never turn assertion or programming errors into skips.
- Revisit CI skips when the cause is fixed. Re-enable the test, run it locally and verify a green CI run before removing its history comment.

## Pull requests

- Use local `git` and `gh`. Push or update a PR only when asked. Direct pushes to `master` require explicit instructions.
- Format changed code before opening or updating a PR. For a feature PR, prefix the title with `feat:` and add a one-line `CHANGELOG.md` entry dated with the PR date (`YYYY-MM-DD`).
- Use the headings **Why**, **Lessons learnt** and **Summary** in PR descriptions and direct-to-master commit messages. Add **Related issues and PRs** for follow-ups and **Unrelated CI and test fixes** when applicable. Omit test plan and verification sections.
- Record the public description style check in the PR checklist. Reuse the review for unchanged copy, review substantive edits again, and mark it N/A for engineering-only changes.
- Squash-merge using the PR description as the commit message. Merge once required non-documentation checks pass. Wait for documentation-only workflows only if the user asks.

## RPC, contracts and chain data

- Use RPC URLs supplied through the user's environment. Check [provider environment settings](eth_defi/provider/env.py) and `eth_defi.chain.CHAIN_NAMES` for names. Ask if the connection cannot be identified. Pass space-separated fallback URLs to `create_multi_provider_web3()`. For single-URL tools, use the first entry.
- Use `web3.eth.block_number` for the head block. Prefer explorers for proxy addresses and Python for explorer queries.
- Before fetching, storing or using an ABI, read the [ABI guide](eth_defi/abi/README.md). Fetch complete verified external ABIs and record their provenance in the folder inventory. Report unavailable verified interfaces. Regenerate integrated contract artefacts with the compiler and record the build source in the inventory. Use `make guard safe-integration` for the guard and Safe contracts.
- Use ABI-backed calls and `EncodedCall.from_contract_call()` for multicalls. Explain any probe for an interface without an available ABI. For ERC-20 amounts and transfers, use `TokenDetails.convert_to_raw()` and `TokenDetails.transfer()`. Prefer `HotWallet` for signing and deployer accounts.
- Read [vault integration](.claude/docs/vault-integration.md) before changing ERC-4626-style adapters, guards or executor integration. Read [Lighter execution](docs/README-lighter-core-execution.md) before changing pool execution or multichain activation scripts.
- Use Hypersync for event discovery and bulk or historical logs. Never use `eth_getLogs` for these reads. Create clients with `configure_hypersync_from_env()` and streams with `open_hypersync_stream()`. For vault discovery use `HypersyncVaultDiscover`. For a targeted query, build a `hypersync.Query` with `LogSelection` as in [vault flow events](eth_defi/vault/flow_events.py).
- Use `fetch_block_timestamps_using_hypersync_cached()` or `fetch_block_timestamps_multiprocess_auto_backend()` with Hypersync for bulk or historical timestamps. Use direct streams only to repair known cache gaps. Preserve `~/.tradingstrategy/block-timestamp/{chain_id}-timestamps.duckdb`. Avoid the retired `~/.tradingstrategy/block-timestamps.*` path.
- Cached readers fill every block in the requested range. Use recorded timestamps or a seeded cache in tests. Only cache-internal tests may call these readers, directly or indirectly, with at most 100 blocks per test. See [dense timestamps](docs/README-dense-timestamps.md). Read [block timestamp storage](docs/README-block-timestamp-storage.md) before changing the writer or running migration 065.
- Scanner RPCs need archive state except on Monad (143), where providers retain a moving window. Use Hypersync for historical events and timestamps. Backfill price gaps promptly from the oldest readable state and preserve older rows. Never retry evicted state or attempt a genesis-to-head state scan. See [Monad backfills](.claude/docs/migration-scripts.md#monad).
- On HyperEVM (999 and 998), the sender's `usingBigBlocks` flag selects the fee market. On live chains, identify large blocks by the [30 million gas limit](eth_defi/hyperliquid/block.py) and price the intended block type with [estimate_gas_price()](eth_defi/gas.py). Avoid raw `eth_gasPrice`, which varies by provider. Skip block-type detection on Anvil forks, which have a single fee market.

## Production data

- For database repairs and persisted accessor changes, add a new [numbered migration](.claude/docs/migration-scripts.md) and include a run reminder in the PR. Port any needed legacy standalone repair to a numbered script. Never run the legacy command or rerun a successful initial migration for a later change. Include a fixed-scope price backfill when discovery, repair or reclassification leaves early history missing.
- Before pushing a numbered migration, verify the [packaged inventory and runner preview](.claude/docs/migration-scripts.md#authoring-a-migration). Direct calls to `migrate(context)` bypass discovery.
- Follow the [production runbook](.claude/docs/migration-scripts.md#production-runbook). Inspect [docker-compose.yml](docker-compose.yml) before maintenance. Stop the looped scanner and other writers before preview or apply. Use the direct Compose command and the image's stamped git commit. Acquire the lifetime guard before the writer lock.
- One-shot and looped scanners share the host's `~/.tradingstrategy` state. Preserve the mount and `HOME`, including metadata, prices, reader state and timestamp caches. Disable historical price scanning for metadata-only repairs. Restore or prepopulate dense timestamps before a historical backfill. If rate-limited, record the response and retry plan before repeating a bulk request.
- Abort on an unreadable or incompatible price Parquet file. Never replace existing data with an empty table. Add nullable columns with `migrate_parquet_schema()` and `pa.nulls()`. Verify schema migrations, including type casts, on a copy of the current production Parquet before merge or deployment.
- Protect `reader-state.pickle`. Losing it moves the scanner's start block backwards and can delete a chain's price history. Preserve Monad rows older than the provider can reconstruct.
- With DuckDB 1.5.0 and Python 3.14, ART-backed `PRIMARY KEY` and `UNIQUE` constraints on large file-backed tables can cause native heap corruption. Use unconstrained staging tables, hash joins and application-level deduplication. Set `wal_autocheckpoint = '1TB'` during bulk writes. See [DuckDB issue #18190](https://github.com/duckdb/duckdb/issues/18190).
- Detect old constraints with `duckdb_constraints()`. Migrate transactionally, preserve every row, and test migration and duplicate ingestion on a production-sized file-backed database before resuming a backfill.

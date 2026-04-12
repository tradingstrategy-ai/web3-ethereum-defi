# Instructions to work with the code base

## Reference docs for Claude

Repo-local reference docs that Claude should consult when the task
touches the relevant area:

- `.claude/docs/gspread.md` — Google Sheets integration test setup. **Read
  this before attempting any Google Sheets automation via the
  Claude-in-Chrome plugin**: in the `tradingstrategy.ai` Workspace
  environment we've tested, sharing a sheet with a service account
  cannot be completed by Claude-in-Chrome and must be performed
  manually by the operator. Other Workspace orgs may behave differently.

## Skills

Repo-local skills live as folders under `.claude/skills`.

When a task matches one of the folder names, open the corresponding `SKILL.md` first and follow it before doing ad hoc exploration.

Skill discovery rules:

- If the user mentions a skill by name, use it.
- If the task clearly matches one of the skill directory names above, use that skill even if the user did not mention it explicitly.
- Start by reading only `.claude/skills/<skill-name>/SKILL.md`.
- If the skill references extra files, open only the files needed for the current task.
- Prefer scripts, templates and checklists referenced by the skill over re-creating the workflow manually.

## English

- Use UK/British English instead of US English
- Say things like `visualise` instead of `visualize`
- For headings, only capitalise the first letter of heading, do not use title case

## Installing dependencies

Install dependencies with all required extras:

```shell
poetry install -E data -E test -E docs -E hypersync -E ccxt -E cloudflare_r2 -E duckdb
```

## Running Python scripts

When running a Python script use `poetry run python` command instead of plain `python` command, so that the virtual environment is activated.

```shell
poetry run python scripts/logos/post-process-logo.py
```

## Running tests

If we have not run tests before make sure the user has created a gitignored file `.local-test.env` in the repository root. This will use `source` shell command to include the actual test secrets which lie outside the repository structure. Note: this file does not contain actual environment variables, just a `source` command to get them from elsewhere. **Never edit this file** and always ask the user to prepare the file for Claude Code.

To run tests you need to use the installed Poetry environment, with given environment secrets file.

To run tests use the `pytest` wrapper command:

```shell
source .local-test.env && poetry run pytest {test case name or pattern here}
```

Always prefix pytest command with relevant source command,
otherwise the test cannot find environment variables.

Avoid running the whole test suite as it takes several minutes. Only run specific test cases.

When running pytest or any test commands, always use an extended timeout
by specifying `timeout: 180000` (3 minutes) in the bash tool parameters.

If you need extra output pass `--log-cli-level=info` argument to `pytest`.

### Environment variable configuration and RPC URL format

We use environment variables like `JSON_RPC_ETHEREUM`, JSON_RPC_ARBITRUM` to interact with various EVM-based blockchains.

In the environment file, the RPC URLs are provided in the project-specific space-separated fallback format, as described in `mev-blocker.rst`.

- If there is a space in RPC URL given by a environment variable like `JSON_RPC_ETHEREUM`, it can be only used with Python call `create_multi_provider_web3()`
- If you are going to use this RPC URL with other commands, like `curl`, you need to parse the RPC environment variable by spltting it by spaces and taking the first entry
- All environment variables point to EVM archive nodes

## Formatting code

Run ruff to format code using Poetry:

```shell
poetry run ruff format
```

## Git worktrees

- For git worktrees, copy `.local-test.env` from the repo root.
- For worktrees, unless you are changing package dependencies, use `poetry run` from the parent repo virtualenv

## Commentary format

Pull request description must have sections:

- Why: the rational of change
- Lessons learnt: memory
- Summary: what was changed

No test plan or verification section. Use Markdown formatting, headings.

## Pull requests

- Only push changes to remote when asked, never update pull requess automatically.
- Never push directly to a master if not told explicitly
- If the user ask to open a pull request as feature then start the PR title with "feat:" prefix and also add one line about the feature into `CHANGELOG.md`
- Each changelog entry should follow the date of the PR in YYYY-MM-DD format. Example: Something was updated (2026-01-01).
- Before opening or updating a pull request, format the code
- When merging pull request, squash and merge commits and use the PR description as the commit message
- If continuous integration (CI) tests fail on your PR, and they are marked flaky, run tests locally to repeat the issue if it is real flakiness or regression

## Pushing to master

- If you push directly to master, the commit message most follow *Commentary format* section

## Specific rules

### Python rules

- We use Python 3.14
- For data structures, prefer `dataclass(slots=True)`
- Use threaded instead of async Python code
- Always type hint function arguments and return values
- Try to use Python and Pandas `apply()` and other functional helpers instead of slow for and while loops
- Use `any()` and `all()` with generators and list comprehension when checking if a collection member has one or more matches, instead of using slow for loops
- All functions that do network reads to get data should be prefixed with `fetch_` instead of `get_`
- Always try to return `Iterator` instead of `list` from a function call to make functions faster
- For long runnign for loops, use `tqdm` and `tqdm_loggable.auto` module for progress bar. As an example, see `lead_scan_core.py`.
- For visualusations, use Plotly. For chart titles, use heading case as explained above.
- Use module level imports, not function level lazy imports, whenever possible
- Never write generic `Exception e:` catch but always catch a specific exception if we can
- Never silently swallow exceptions and th

### Code comments

- For code comments, Use Sphinx restructured text style
- For documenting dataclass and Enum members, use Sphinx `#: comment here` line comment above variable, not `:param:`
- If a. class function overloads a function inherited from the parent, and there is nothing to comment, do not repeat the code comment and leave it empty instead
- Each function should *minimum* have 1) one liner summary description 2) one paragraph longer description 3) arguments and return values documented 4) for APIs and integrations, links to authoritative and canonical documentation 5) for dataframes and series expectations of columns and value types

### Type hinting

- Use `HexAddress` instead of `str` for blockchain addresses
- For percent like numbers, do not use raw float, but use `eth_defi.types.Percent` type alias

### Logging

- For logging, use the module level `logger = logging.getLogger(__name__)` pattern
- When logging using `logger.info()`, `logging.debug()` or similar,
  prefer %s and %f unexpanded string syntax instead of Python string interpolation, because of performance reasons

### Documentation

- All API modules should have stub entry under `docs/source/api` and cross-referenced in `docs/source/api/index` table of contents
- See `docs/source/api/index.rst` and `docs/source/api/lagoon/index.rst` as examples
- When writing documentation, in sentences, include inline links to the source pages. Link each page only once, preferably earler in the text.

### datetime

- Use naive UTC datetimes everywhere
- When using datetime class use `import datetime.datetime` and use `datetime.datetime` and `datetime.timedelta` as type hints
- Instead of `datetime.datetime.utcnow()` use `native_datetime_utc_now()` that is compatible across Python versions

### Enum

- For string enums, both members and values must in snake_case

### Pytest

- Never use test classes in pytest
- `pytest` tests should not have stdout output like `print`
- Instead of manual float fuzzy comparison like `assert abs(aave_total_pnl - 96.6087) < 0.01` use `pytest.approx()`
- For DuckDB testing, make sure the database is always closed using finally clause or fixtures
- Always use fixture and test functions, never use test classes
- For Anvil mainnet fork based tests, whici use a fixed block number, in asserts check for absolute number values instead of relative values like above zero, because values never change.
  Expect for Monad, as Monad blockchain does not support archive nodes and historical state.
- For reuseable testing code, use `testing` modules under `eth_defi` - do not nyt try to import "tests" as it does not work with pytest

### pyproject.toml

- When adding or updating dependencies in `pyproject.toml`, always add a comment why this dependency is needed for this project

## Python notebooks

- Whenever possible, prefer table output instead of print(). Use Pandas DataFrame and notebook's built-in display() function to render tabular data.

## Command line scripts

- Use scripts/erc-4626/scab-vaults.py as an example how to set up logger and read any needed environment variables as input.
- Always use environment variables. Do not attempt to create command line parsers unless explicitly asked.
- For tabular output, do not use `print()` loops but use `tabulate.tabulate()` function, see `whitelist-vaults.py` as an example

## Parallerisation and optimising long running data reading pipelines

- Uses `joblib.Parallel` to parallerise API reading of multiple entries
- Use threading backend unless explicitly specified otherwise
- For example, see `lead_scan_core.py`
- All functions using `joblib.Parallel` must take `max_workers` argument. This must be exposed to command line scripts as `MAX_WORKERS` environment variable, see `scripts/erc-4626/scan-vaults.py` as an example.

## Working with RPC and blockchain explorers

- Prefer a blockchain explorer like Etherscan over Python and Curl when trying to read proxy contract address
- Prefer Python snippets instead of `curl` when trying to read data directly from a blockchain explorer
- To get the latest block number, use given JSON-RPC URL and Python's Web3.py `web3.eth.block_number` call
- Never try to figure out RPC URL yourself - always use environment variables from the local environment given by the user. See `eth_defi.chain.CHAIN_NAMES` for aliases like chain id 999 -> JSON_RPC_HYPERLIQUD. Stop and ask user if you cannot figure out.

For JSON-RPC URL configuration, environment variables. The variables are in the format `JSON_RPC_{CHAIN}` where `{CHAIN}` is the uppercase chain name:

- `JSON_RPC_ETHEREUM` - Ethereum mainnet
- `JSON_RPC_ARBITRUM` - Arbitrum One
- `JSON_RPC_BASE` - Base
- `JSON_RPC_POLYGON` - Polygon
- `JSON_RPC_BINANCE` - BNB Smart Chain a.k.a. Binance a.k.a. BNB chain
- `JSON_RPC_HYPERLIQUID` - HyperEVM

You chan find these in `CHAIN_NAMES` and in `eth_defi.provider.env`

## Building integrated smart contracts

You can use `Makefile` commands `make guard safe-integration` to rebuild smart contracts for Satfe and Lagoon integration.
**Never edit ABI JSON FILES directly**. Always build them with a compiler.

## Documentation

Documentation uses Sphinx v4.5 for API and narrative documentation and lives in `docs` folder.

You can build the documentation with the command:

```shell
source .local-test.env && make build-docs
```

If you need to clean Sphinx's autosummaries you can run:

```shell
source .local-test.env && make build-docs
```

Never directly edit auto-generated sphinx files in `_autosummary*` folders.

## Parquet schema migrations

The vault price pipeline accumulates months of historical data in `vault-prices-1h.parquet`. Losing this data requires days of re-scanning from archive nodes.

- **Never silently discard existing data.** If a schema migration (`migrate_parquet_schema()`, `cast()`) fails, the pipeline must abort with a hard error — never fall back to `existing_table = None`. Silent data loss is worse than a crash.
- **Never catch `ArrowInvalid` and reset to empty.** If the existing parquet cannot be read or migrated, raise the exception so the operator can restore from a backup.
- **New columns must have null defaults.** Add them via `migrate_parquet_schema()` with `pa.nulls()`. Never require a value in existing rows.
- **Type changes need explicit migration.** If changing a column's type (e.g. `uint32` → `uint64`), verify `cast()` works on production data before merging — test with a copy of the real parquet, not just synthetic test data.
- **Always test schema changes against the production parquet.** Download the current file and verify the migration path locally before deploying.
- **Reader state loss causes full data wipe.** The scanner deletes existing chain rows from `start_block` onwards. If `reader-state.pickle` is lost, `start_block` falls back to the earliest vault block, deleting all historical data for that chain. Treat reader state files as critical production state.

## ERC-20

- Don't do hardcoded token decimal multiply, use `TokenDetails.convert_to_raw()`
- Use `TokenDetails.transfer()` and similar - do not do raw ERC-20 contract calls unless needed
- Use `eth_defi.hotwallet.HotWallet` for deployer accounts and signing transactions when possible

## Web Fetching and 403

When fetching web pages, if `web_fetch` returns a 403 error, retry the request using the Chrome MCP tool to load the page in a real browser instead.

Prerequisites:

1. **Claude in Chrome extension** (v1.0.36+) - [Chrome Web Store](https://chromewebstore.google.com/detail/claude/fcoeoabgfenejglbffodgkkbkcdhcgfn)
2. **Google Chrome** running
3. **Direct Anthropic plan** (Pro, Max, Team, or Enterprise)


Browser tools are automatically available when the Chrome extension is connected. Use `@browser` in your Visual Studio Code prompt to activate the connection.

When using browser tools, Claude may ask for permission to visit specific domains. **Approve these prompts** to allow browser automation. You can also pre-approve domains in the Chrome extension settings.

## README files in the repository

Consult these for domain-specific context. Logo READMEs under `eth_defi/data/vaults/original_logos/*/README.md` document logo source URLs per protocol.

| Path | Description |
|------|-------------|
| `README.md` | Web3-Ethereum-Defi project overview |
| `contracts/guard/README.md` | GuardV0 — on-chain trade validation for asset management |
| `contracts/in-house/README.md` | Web3-Eth-Defi integration contracts |
| `contracts/safe-integration/README.md` | Trading Strategy Zodiac-module for Safe multisig wallets |
| `docs/README-Hypercore-guard.md` | Hypercore native vault guard integration |
| `docs/README-contract-size.md` | Contract sizes and compiler optimisation |
| `docs/derive-onboarding/README-derive-trader.md` | Derive session key for vault traders |
| `docs/protocol-research/README.md` | AI-assisted vault protocol discovery notes |
| `docs/source/api/derive/README.md` | Derive.xyz integration — implementation summary |
| `eth_defi/aave_v3/README.md` | About Aave v3 integration |
| `eth_defi/abi/ipor/README.md` | IPOR ABI source links |
| `eth_defi/abi/lagoon/README.md` | Lagoon ABI source links |
| `eth_defi/abi/uniswap-swap-contracts/README.md` | SwapRouter02 deployment on Base |
| `eth_defi/cctp/README-cctp.md` | Circle CCTP V2 integration |
| `eth_defi/data/vaults/README.md` | Vault protocol metadata and logo system |
| `eth_defi/erc_4626/vault_protocol/README-reader-states.md` | Vault reader states and warmup system |
| `eth_defi/erc_4626/vault_protocol/README-utilisation.md` | Utilisation and available liquidity metrics for lending vaults |
| `eth_defi/gmx/README-GMX-Lagoon.md` | GMX Lagoon integration security analysis |
| `eth_defi/gmx/README.md` | GMX CCXT adapter for eth_defi |
| `eth_defi/gmx/ccxt/README.md` | GMX CCXT adapter implementation |
| `eth_defi/gmx/graphql/README.md` | GMX Subsquid GraphQL integration |
| `scripts/base/README.md` | Base chain related manual test scripts |
| `scripts/debian-bullseye-compatibility/README.md` | Running on Debian Bullseye |
| `scripts/erc-4626/README-vault-scripts.md` | ERC-4626 vault scripts |
| `scripts/grvt/README-grvt-vaults.md` | GRVT native vault metrics pipeline |
| `scripts/hyperliquid/README-hyperliquid-copy-trading.md` | Hyperliquid copy trading platforms and HFT account identification |
| `scripts/hyperliquid/README-hyperliquid-vaults.md` | Hyperliquid native vault metrics pipeline |
| `scripts/hyperliquid/README-hyperliquid-vaults-high-frequency.md` | High-frequency Hyperliquid vault data fetcher |
| `scripts/lighter/README-lighter-vaults.md` | Lighter native pool metrics pipeline |
| `tests/erc_4626/vault_protocol/README.md` | Vault protocol detection tests (mainnet-fork) |
| `tests/gmx/README.md` | Testing for GMX |
| `tests/guard/README.md` | Integration tests for GuardV0 and TradingStrategyModuleV0 |
| `tests/provider/README.md` | Service provider integration tests |
| `tests/rpc/README.md` | JSON-RPC scenario tests |

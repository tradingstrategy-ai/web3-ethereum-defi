# Instructions to work with the code base

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

## Pull requests

- Only commit and push when asked. Never commit without explicit permission.
- Never push directly to a master, and open a pull request when asked.
- Do not include test plan in a pull request description
- If the user ask to open a pull request as feature then start the PR title with "feat:" prefix and also add one line about the feature into `CHANGELOG.md`
- Each changelog entry should follow the date of the PR in YYYY-MM-DD format and then the pull request id with a link to the pull request. Example: Something was updated (2026-01-01, [#666](http://example.com)).
- If this is a major feature instead of minor fix, use **major** bold suffix and then put them to the top of changelog list. Put minor features bottom.
- Before opening or updating a pull request, format the code
- When merging pull request, squash and merge commits and use the PR description as the commit message. If there is a related changelog entry, link it to the closed PR.
- When editing pull request title or body, use `gh api` REST endpoint instead of `gh pr edit` which uses a deprecated GraphQL API that fails on repos with classic projects. Example: `gh api repos/OWNER/REPO/pulls/NUMBER -X PATCH -f title="..." -f body="..."`

## Specific rules

### Python rules

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

### Code comments

- For code comments, Use Sphinx restructured text style
- For documenting dataclass and Enum members, use Sphinx `#: comment here` line comment above variable, not `:param:`
- If a. class function overloads a function inherited from the parent, and there is nothing to comment, do not repeat the code comment and leave it empty instead

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
| `scripts/hyperliquid/README-hyperliquid-vaults.md` | Hyperliquid native vault metrics pipeline |
| `scripts/lighter/README-lighter-vaults.md` | Lighter native pool metrics pipeline |
| `tests/erc_4626/vault_protocol/README.md` | Vault protocol detection tests (mainnet-fork) |
| `tests/gmx/README.md` | Testing for GMX |
| `tests/guard/README.md` | Integration tests for GuardV0 and TradingStrategyModuleV0 |
| `tests/provider/README.md` | Service provider integration tests |
| `tests/rpc/README.md` | JSON-RPC scenario tests |

## GitHub PR reviews

Claude responds to `@claude` mentions in GitHub PR comments, issues, and reviews. Reviews are **never** triggered automatically — always wait for an explicit `@claude` mention.

### Review workflow

When reviewing a PR, follow these seven phases in order:

#### Phase 1: Establish scope baseline

Determine what the PR is supposed to do **before** reading any code:

1. Read the PR title — verify it follows conventional commits format (must start with `feat:`, `fix:`, `perf:`, `chore:`, `refactor:`, `docs:`, or `test:`). Flag as **BLOCKING** if missing.
2. Read the PR description in full
3. Read any linked issues (`Fixes #N`, `Closes #N`)
4. Search the branch for `plan.md` or `spec.md` — use as primary scope reference if found
5. Fall back to commit messages if no other scope source exists
6. Summarise the intended scope as a numbered checklist of requirements — this checklist drives all subsequent phases

#### Phase 2: Gather changes

1. Run `gh pr diff` to read the full diff
2. Run `gh pr view` to get metadata (author, labels, reviewers, CI status)
3. List all modified files and categorise them (source, tests, config, docs)
4. Identify the blast radius — which modules/packages are affected
5. Note any files that were deleted or renamed

#### Phase 3: Requirements validation

Map each requirement from the Phase 1 checklist to the implementation:

1. For each requirement, identify which files and changes implement it
2. Flag requirements with **no** corresponding implementation as **IN-SCOPE**
3. Flag changes that do **not** map to any stated requirement — classify as **SUGGESTION** (if useful) or **IGNORE** (if unnecessary)
4. Update the scope checklist with pass/fail status for each requirement

#### Phase 4: Code review

Review **only lines modified in this PR** — do not review unchanged code:

1. Check for bugs, off-by-one errors, and incorrect logic
2. Check for security issues (injection, hardcoded secrets, unsafe deserialisation)
3. Verify CLAUDE.md compliance (type hints, naming conventions, import style, docstrings)
4. If `pyproject.toml` or `__version__` changed, run version validation (see below)
5. Check that code comments explain **why**, not what
6. Classify each finding using the scope classification table below

#### Phase 5: Backlog triage

For any finding classified as **BACKLOG**:

1. Note it in the review summary with enough context for a future issue
2. Do not block the PR on backlog items — they are explicitly out of scope
3. If the backlog item is significant enough, suggest the author creates a GitHub issue to track it

#### Phase 6: Generate report

1. Drop anything classified as **IGNORE**
2. Post the review using the report structure below
3. Include the scope checklist from Phase 3 as the "Scope compliance" section
4. Group remaining findings by classification: BLOCKING first, then IN-SCOPE, then SUGGESTION
5. End with a clear recommendation: APPROVE / APPROVE WITH CHANGES / REQUEST CHANGES

**Quality gates for approval:** all requirements implemented, zero BLOCKING findings, all IN-SCOPE issues resolved or acknowledged, backlog items noted.

#### Phase 7: Knowledge capture

After posting the review, check if any findings are worth preserving:

1. If a finding exposes a **missing convention** (e.g. a type hint pattern, naming rule, or import style that should be standardised), flag it as **BACKLOG** and suggest a CLAUDE.md update
2. If a finding reveals a **recurring mistake** across multiple files, note it in the review summary so the author can check other files too
3. If a finding involves an **architectural decision** with non-obvious rationale, suggest the author add a code comment or docstring explaining the decision

Do not attempt to modify CLAUDE.md directly during a review — suggest it and let the maintainer decide.

### Scope classification

Every finding in a review must be classified:

| Label | Meaning | Action |
|-------|---------|--------|
| **BLOCKING** | Bug, security issue, or regression introduced by this change | Must fix before merge |
| **IN-SCOPE** | Issue directly related to stated requirements | Should address in this PR |
| **SUGGESTION** | Improvement within changed code, not required | Author decides |
| **BACKLOG** | Good idea but outside PR scope | Create a GitHub issue |
| **IGNORE** | Nitpick, style preference, or not worth tracking | Skip entirely |

A PR review validates scope compliance, not code perfection. Improvements beyond scope belong in future PRs.

### Anti-patterns to avoid

- Do not flag issues in **unchanged** code — create a separate issue instead
- Do not block on style preferences — use ruff/linters for style
- Do not suggest "while you're here" refactors — those are BACKLOG items
- Do not demand perfection when requirements are met
- Do not expand scope beyond stated requirements — improvements belong in future PRs
- Do not duplicate what linters and type checkers catch — assume CI runs ruff and mypy
- Do not flag intentional behaviour changes that are directly related to the PR's purpose
- Do not re-review lines that were not modified in the diff

### Review report structure

```markdown
## PR review: #<number> <title>

### Scope compliance
Requirements from PR description / plan.md / linked issues:
- [x] Requirement A — implemented
- [ ] Requirement B — **missing**

### Blocking (N)
1. [BLOCKING] Description — `file.py:line`

### In-scope (N)
1. [IN-SCOPE] Description — `file.py:line`

### Suggestions (N)
1. [SUGGESTION] Description — `file.py:line`

### Recommendation
APPROVE / APPROVE WITH CHANGES / REQUEST CHANGES
```

### Version validation

When a PR changes `pyproject.toml` or `__version__`, verify:
- `pyproject.toml` version matches `__version__` in `eth_defi/__init__.py` (if present)
- `CHANGELOG.md` has an entry for the new version
- Version follows semver format

### Code comment quality

When reviewing code comments, check:
- Comments explain **why**, not what the code does
- No commented-out dead code (delete instead — git preserves history)
- No stale TODOs without issue references
- Public functions/classes have Sphinx docstrings

Comments are warranted for:
- Non-obvious behaviour and edge cases
- Business logic decisions grounded in domain knowledge
- Performance optimisation rationale
- Workarounds that require context to understand
- External constraints or limitations (e.g. API quirks, protocol-specific behaviour)

Comments are **not** needed for:
- Self-explanatory code with clear naming
- Standard patterns (CRUD, boilerplate, well-known idioms)
- Code that is already documented by its type hints and function signature

When suggesting comment changes in a review:
- Flag stale comments that contradict the current code as **IN-SCOPE**
- Flag missing context on complex logic as **SUGGESTION**, not **BLOCKING**
- Never demand comments on straightforward code


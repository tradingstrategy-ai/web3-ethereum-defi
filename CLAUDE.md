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

- Never push directly to a master, and open a pull request when asked.
- Do not include test plan in a pull request description
- If the user ask to open a pull request as feature then start the PR title with "feat:" prefix and also add one line about the feature into `CHANGELOG.md`
- Each changelog entry should follow the date of the PR in YYYY-MM-DD format and then the pull request id with a link to the pull request. Example: Something was updated (2026-01-01, [#666](http://example.com)).
- If this is a major feature instead of minor fix, use **major** bold suffix and then put them to the top of changelog list. Put minor features bottom.
- Before opening or updating a pull request, format the code
- When merging pull request, squash and merge commits and use the PR description as the commit message

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
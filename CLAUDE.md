# Instructions to work with the code base

## English

- Use UK/British English instead of US English
- Say things like `visualise` instead of `visualize`
- For headings, only capitalise the first letter of heading, do not use title case

## Running tests

If we have not run tests before make sure the user has created a gitignored file `.local-test.env` in the repository root. This will use `source` shell command to include the actual test secrets which lie outside the repository structure. Note: this file does not contain actual environment variables, just a `source` command to get them from elsewhere. **Never edit this file** and always ask the user to prepare the file for Claude Code.

To run tests you need to use the installed Poetry environment, with given environment secrets file.

To run tests use the `pytest` wrapper command:

```shell
source .local-test.env && poetry pytest run {test case name or pattern here}
```

Always prefix pytest command with relevant source command,
otherwise the test cannot find environment variables.

Avoid running the whole test suite as it takes several minutes. Only run specific test cases.

When running pytest or any test commands, always use an extended timeout
by specifying `timeout: 180000` (3 minutes) in the bash tool parameters.

## Formatting code

After a large task is complete you can format the code with:

```shell
poetry run ruff format
```

## Specific rules

### Generic

- For data structures, prefer `dataclass(slots=True)`
- Use threaded instead of async Python code
- Always type hint function arguments and return values
- Try to use Python and Pandas `apply()` and other functional helpers instead of slow for and while loops
- Use `any()` and `all()` with generators and list comprehension when checking if a collection member has one or more matches, instead of using slow for loops
- All functions that do network reads to get data should be prefixed with `fetch_` instead of `get_`
- Always try to return `Iterator` instead of `list` from a function call to make functions faster

### Code comments

- For code comments, Use Sphinx restructured text style
- For documenting dataclass and Enum members, use Sphinx `#: comment here` line comment above variable, not `:param:`

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

## Progress

- For long runnign for loops, use `tqdm` and `tqdm_loggable.auto` module for progress bar
- For example, see `lead_scan_core.py`

## Visualisations

- Use Plotly
- For chart titles, use heading case as explained above

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

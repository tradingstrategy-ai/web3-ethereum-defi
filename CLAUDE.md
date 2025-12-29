# Instructions to work with the code base

## Running tests

To run tests you need to use the installed Poetry environment, with given environment secrets file.

To run tests use the `pytest` wrapper command:

```shell
source ~/code/trade-executor/env/local-test.env && poetry pytest run
```

Avoid running the whole test suite as it takes several minutes. Only run specific test cases.

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

### datetime

- Use naive UTC datetimes everywhere
- When using datetime class use `import datetime.datetime` and use `datetime.datetime` and `datetime.timedelta` as type hints

### Enum

- For string enums, both members and values must in snake_case

### Pytest

- Never use test classes in pytest
- `pytest` tests should not have stdout output like `print`

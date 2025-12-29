# Instructions to work with the code base

## Running tests

To run tests you need to use the installed Poetry environment, with given environment secrets file.

To run tests use the `pytest` wrapper command:

```shell
source ~/code/trade-executor/env/local-test.env && poetry pytest run
```

Avoid running the whole test suite as it takes several minutes. Only run specific test cases.

## Specific rules

- For data structures, prefer `dataclass(slots=True)`
- Use `HexAddress` instead of `str` for blockchain addresses
- Use threaded instead of async Python code
- For code comments, Use Sphinx restructured text style
- For documenting dataclass and Enum members, use Sphinx `#: comment here` line comment above variable, not `:param:`
- Always type hint function arguments and return values
- Try to use Python and Pandas `apply()` and other functional helpers instead of slow for and while loops
- For logging, use the module level `logger = logging.getLogger(__name__)` pattern
- For percent like numberes, do not use raw float, but
- All functions that do network reads to get data should be prefixed with `fetch_` instead of `get_`

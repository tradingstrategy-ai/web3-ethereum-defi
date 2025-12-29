# Instructions to work with the code base

## Running tests

To run tests you need to use the installed Poetry environment, with given environment secrets file.

To run tests use the `pytest` wrapper command:

```shell
source ~/code/trade-executor/env/local-test.env && poetry pytest run
```

## Specific rules

- For data structures, prefer `dataclass(slots=True)`
- Use `HexAddress` instead of `str` for blockchain addresses

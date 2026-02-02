# Derive.xyz Tests

## Overview

This directory contains tests for the Derive.xyz integration. The tests use mocked API responses to verify correct behavior without requiring actual API credentials.

## Test Files

- **test_account.py** - Tests for account balance and collateral reading with mocked responses
- **conftest.py** - Test fixtures for mock testing (no credentials needed)

## Running Tests

### Run All Mock Tests (No Credentials Required)

```bash
poetry run pytest tests/derive/test_account.py -v
```

Expected output:
```
tests/derive/test_account.py::test_empty_account_returns_empty_collaterals PASSED
tests/derive/test_account.py::test_empty_account_summary_returns_zero_value PASSED
tests/derive/test_account.py::test_funded_account_returns_collateral_data PASSED
tests/derive/test_account.py::test_api_error_handling PASSED
tests/derive/test_account.py::test_missing_session_key_raises_error PASSED
tests/derive/test_account.py::test_partial_collateral_data_handling PASSED
```

## Test Coverage

### Empty Account Behavior

1. **test_empty_account_returns_empty_collaterals**
   - Verifies empty accounts return empty list `[]`
   - No errors or exceptions

2. **test_empty_account_summary_returns_zero_value**
   - Verifies account summary shows `Decimal("0")` for total value
   - All fields properly initialized even with no balance

### Funded Account Behavior

3. **test_funded_account_returns_collateral_data**
   - Tests proper parsing of collateral data
   - Verifies USDC and WETH balances
   - Checks locked vs available calculations

### Error Handling

4. **test_api_error_handling**
   - Verifies proper error propagation from API

5. **test_missing_session_key_raises_error**
   - Ensures authentication is required

6. **test_partial_collateral_data_handling**
   - Tests graceful handling of missing optional fields

## Key Features Tested

✅ Empty account returns empty collaterals list
✅ Empty account summary shows zero value
✅ Funded accounts return proper collateral data
✅ Decimal type used for all financial values
✅ Missing fields handled gracefully with defaults
✅ Authentication required (session key check)
✅ API errors propagate correctly

## Integration Testing (Future)

When API credentials are available, the original fixtures in `conftest.py` can be used:

```bash
# Set environment variables
export DERIVE_OWNER_PRIVATE_KEY=0x...
export DERIVE_WALLET_ADDRESS=0x...
export SEND_REAL_REQUESTS=true

# Run integration tests (currently skipped without credentials)
source .local-test.env && poetry run pytest tests/derive/ -v
```

## Mock Test Benefits

1. **No Credentials Required** - Tests run without API access
2. **Predictable** - Mocked responses ensure consistent test results
3. **Fast** - No network calls, tests complete in milliseconds
4. **Comprehensive** - Cover both empty and funded account scenarios
5. **CI-Friendly** - Can run in any environment without setup

## Implementation Details

The mock tests use Python's `unittest.mock` to patch the `_make_jsonrpc_request` method of `DeriveApiClient`. This allows testing the full code path while controlling API responses.

Example mock response for empty account:
```python
{"collaterals": []}
```

Example mock response for funded account:
```python
{
    "collaterals": [
        {
            "currency": "USDC",
            "available": "100.50",
            "total": "100.50",
            "locked": "0",
            "token_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        }
    ]
}
```
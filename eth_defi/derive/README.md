# Derive.xyz Integration Notes

## Overview

Derive.xyz (formerly Lyra) is a decentralised perpetuals and options exchange built on Derive Chain (OP Stack L2). This integration provides session key authentication and account balance reading capabilities.

## Authentication Architecture

### Three-Tier Wallet System

Derive uses a unique wallet architecture:

1. **Owner EOA** - Your original Ethereum wallet (signs session key registrations)
2. **Derive Wallet** - Smart contract wallet on Derive Chain (NOT your EOA address)
3. **Session Keys** - Temporary Ethereum wallets granted API access permissions

### Finding Your Derive Wallet Address

The Derive Wallet is a smart contract wallet created by Derive.xyz, distinct from your EOA:

- Visit https://testnet.derive.xyz/ (or mainnet)
- Navigate to: Home → Developers → "Derive Wallet"
- This is the address you'll use as `derive_wallet_address`

### Session Key Permissions

Three permission levels available:

- **read_only** - View account data, orders, and history (no modifications)
- **account** - Manage settings, cancel orders, but no trading/withdrawals
- **admin** - Full access including trading, deposits, and withdrawals

## API Structure

### JSON-RPC 2.0 Protocol

Derive API uses JSON-RPC 2.0 over HTTP POST:

**Request format:**
```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "private/get_collaterals",
    "params": {"subaccount_id": 1}
}
```

**Success response:**
```json
{
    "id": 1,
    "result": {...}
}
```

**Error response:**
```json
{
    "id": 1,
    "error": {
        "code": -32600,
        "message": "Authentication required"
    }
}
```

### Authentication Headers

Authenticated requests require:
```python
headers = {
    "Content-Type": "application/json",
    "X-LyraWallet": derive_wallet_address,  # Required
    "X-Timestamp": str(timestamp_ms),
    "X-SessionKey": session_key_address,
    "X-Signature": signature_hex,
}
```

### API Endpoints Used

- `private/register_session_key` - Register new session key with owner signature
- `private/get_collaterals` - Fetch collateral balances
- `private/get_account` - Fetch account information
- `private/get_margin` - Fetch margin requirements

## Implementation Details

### Session Key Registration Flow

1. **Generate Session Key**: Create new Ethereum wallet (`Account.create()`)
2. **Build EIP-712 Message**: Structure containing wallet, session key, scope, expiry
3. **Sign with Owner**: Owner EOA signs the EIP-712 message
4. **Submit to API**: POST to `private/register_session_key` with signature
5. **Store Credentials**: Save session key private key for subsequent requests

### Request Signing Pattern

For authenticated requests:
1. Create message: `timestamp + method + sorted_json_params`
2. Hash message: `Web3.keccak(text=message)`
3. Sign with session key: `session_account.signHash(message_hash)`
4. Include in headers: `X-Signature`, `X-Timestamp`, `X-SessionKey`

### Rate Limiting

- Uses SQLite-backed rate limiting for thread-safe parallel requests
- Default: 2 requests per second (conservative estimate)
- Database location: `~/.tradingstrategy/derive/rate-limit.sqlite`
- Compatible with `joblib.Parallel` threading backend

## Empty Account Testing

The integration supports testing with completely empty accounts (zero balance):

### Why Test With Empty Accounts?

1. **API Verification** - Ensures API handles empty accounts gracefully
2. **No Funding Required** - Can test without depositing funds
3. **Realistic Scenario** - New users often start with empty accounts
4. **Error Handling** - Verifies proper error messages for insufficient balance

### Test Cases

**test_empty_account_balance()** - Tests with the configured account (may or may not be empty)
```python
def test_empty_account_balance(session_key_client):
    """Works whether account is empty or funded."""
    collaterals = fetch_account_collaterals(session_key_client)
    # Returns empty list [] for zero balance
    # Returns collateral data if funded
```

**test_fresh_empty_account_with_zero_balance()** - Tests with guaranteed empty account
```python
def test_fresh_empty_account_with_zero_balance(empty_account_client):
    """Requires DERIVE_EMPTY_WALLET_ADDRESS env variable."""
    collaterals = fetch_account_collaterals(empty_account_client)
    assert len(collaterals) == 0  # Guaranteed empty

    summary = fetch_account_summary(empty_account_client)
    assert summary.total_value_usd == Decimal("0")  # Zero balance
```

### Expected Behaviour With Empty Accounts

- `fetch_account_collaterals()` returns empty list `[]`
- `fetch_account_summary()` returns zero values
- No errors or exceptions raised
- All API calls succeed normally

## Testing Notes

### Environment Variables

Required for integration testing:
```bash
DERIVE_OWNER_PRIVATE_KEY=0x...       # Owner wallet private key
DERIVE_WALLET_ADDRESS=0x...          # Derive smart contract wallet
DERIVE_SESSION_KEY_PRIVATE=0x...     # Optional, auto-generated if missing
DERIVE_EMPTY_WALLET_ADDRESS=0x...    # Optional, for testing with empty accounts
SEND_REAL_REQUESTS=true              # Enable live API calls
```

### Creating Test Accounts

To create a fresh test account on Derive testnet:

1. **Generate Wallet**: `Account.create()` in Python
2. **Fund with ETH**: Use Sepolia faucet for gas
3. **Connect to Derive**: Visit https://testnet.derive.xyz/ and connect wallet
4. **Mint USDC**: Use Derive interface or Circle testnet faucet
5. **Get Derive Wallet**: Find address in Developers section
6. **Register Session Key**: Use `client.register_session_key()`

### Test Fixtures

The test suite provides auto-registering session keys:

- `owner_account` - Owner wallet fixture
- `derive_client` - Basic client with owner account
- `session_key_client` - Client with auto-registered session key
- `test_account_wallet` - Fresh wallet for new account creation
- `empty_account_client` - Client for guaranteed empty account with zero balance

### Running Tests

```bash
# All tests
source .local-test.env && poetry run pytest tests/derive/ -v

# Specific test
source .local-test.env && poetry run pytest tests/derive/test_authentication.py::test_register_session_key -v

# With detailed logging
source .local-test.env && poetry run pytest tests/derive/ -v --log-cli-level=info
```

## Supported Features

### Current Implementation

✅ Session key registration with EIP-712 signing
✅ Authenticated JSON-RPC 2.0 requests
✅ Account balance reading (collaterals)
✅ Account summary with margin information
✅ Thread-safe rate limiting
✅ Exponential backoff retry logic

### Supported Collateral Types

- USDC (USD Coin)
- wETH (Wrapped Ethereum)
- wstETH (Wrapped Liquid Staked Ethereum)
- wBTC (Wrapped Bitcoin)

### Not Yet Implemented

⏭️ Account/subaccount creation via API
⏭️ Position reading and management
⏭️ Order placement and management
⏭️ Trading functionality
⏭️ Withdrawal operations
⏭️ WebSocket support for real-time data

## Known Limitations

1. **API Documentation Gaps**: Some endpoints may require adjustment based on actual API responses
2. **EIP-712 Message Structure**: The exact message structure for session key registration may need refinement based on API documentation
3. **Account Creation**: Programmatic account creation needs further research (currently done via web interface)
4. **Subaccount Creation**: The `create_subaccount()` method is stubbed but not fully implemented

## Code Patterns

### Using Decimal for Financial Values

Always use `Decimal` for financial amounts:
```python
from decimal import Decimal

balance = Decimal(str(response["amount"]))  # Convert to string first
total = balance + Decimal("100.50")
```

### Error Handling

Functions raise `ValueError` with descriptive messages:
```python
if not client.session_key_private:
    raise ValueError("Session key required. Call client.register_session_key() first.")
```

### Logging Pattern

Use module-level logger with %s formatting:
```python
logger = logging.getLogger(__name__)
logger.info("Fetching collaterals for subaccount %s", subaccount_id)
```

## External Resources

### Official Documentation
- [Derive.xyz Platform](https://www.derive.xyz/)
- [Testnet Interface](https://testnet.derive.xyz/)
- [API Documentation](https://docs.derive.xyz/)
- [API Reference](https://docs.derive.xyz/reference/overview)
- [Session Keys Guide](https://docs.derive.xyz/reference/session-keys)
- [JSON-RPC Documentation](https://docs.derive.xyz/reference/json-rpc)

### Development Tools
- [Python Signing SDK](https://github.com/derivexyz/v2-action-signing-python)
- [Rust SDK](https://github.com/derivexyz/cockpit)
- [Derive Chain Explorer](https://explorer.derive.xyz/)

### Testing Resources
- [Circle USDC Testnet Faucet](https://faucet.circle.com/)
- [Sepolia ETH Faucet](https://sepoliafaucet.com/)

## Implementation History

**Date**: 2026-02-03
**Version**: 0.39
**Based on**: Orderly authentication pattern + Hyperliquid session pattern

### Key Design Decisions

1. **Authentication Pattern**: Followed `OrderlyApiClient` pattern from eth_defi.orderly for EIP-712 signing
2. **Session Management**: Reused Hyperliquid's session.py for proven rate limiting implementation
3. **Data Structures**: Used `@dataclass(slots=True)` with proper type hints following codebase standards
4. **Error Messages**: Clear messages guide users to set up authentication properly
5. **Test Strategy**: Auto-register session keys in fixtures to simplify testing

### Reference Implementations

- `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/eth_defi/orderly/api.py` - Authentication pattern
- `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/eth_defi/hyperliquid/session.py` - Session management
- `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/tests/orderly/conftest.py` - Test fixture pattern

## Troubleshooting

### Common Issues

**"Session key required" Error**:
- Ensure you've called `client.register_session_key()` first
- Set `client.session_key_private` with the returned private key

**"derive_wallet_address required" Error**:
- You must provide the Derive Wallet address (smart contract wallet)
- This is NOT your EOA address - get it from Derive.xyz interface

**Authentication Failures**:
- Check that `DERIVE_OWNER_PRIVATE_KEY` is set correctly
- Verify `DERIVE_WALLET_ADDRESS` matches your Derive Wallet
- Ensure session key hasn't expired (default: 24 hours)

**Rate Limiting**:
- SQLite database locks indicate concurrent access is working correctly
- Default 2 req/sec should be safe for most use cases
- Increase `requests_per_second` if API limits allow

### Debug Logging

Enable detailed logging to troubleshoot issues:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Or in pytest:
```bash
pytest tests/derive/ -v --log-cli-level=debug
```

## Future Enhancements

Potential improvements for future versions:

1. **Full Account Creation**: Implement programmatic account and subaccount creation
2. **Trading Support**: Add order placement and management
3. **Position Management**: Read and modify open positions
4. **WebSocket Integration**: Real-time data streaming
5. **Advanced Session Management**: Session key renewal, scope elevation
6. **Batch Operations**: Parallel collateral reading for multiple subaccounts

## Contributing

When extending this integration:

1. Follow existing code patterns (see authentication.py and account.py)
2. Use `Decimal` for all financial values
3. Add comprehensive docstrings with examples
4. Include tests with proper fixtures
5. Update this README with new features
6. Maintain British English in documentation
7. Run `ruff format` before committing

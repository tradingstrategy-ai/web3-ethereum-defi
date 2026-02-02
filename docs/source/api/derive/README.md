# Derive.xyz Integration - Implementation Summary

## Overview

Successfully implemented comprehensive Derive.xyz perpetuals and options DEX integration with session key authentication.

## What Was Implemented

### Core Modules (`eth_defi/derive/`)

1. **constants.py** - API URLs, rate limits, and enums
   - Mainnet/testnet URLs
   - Session key scopes (read_only, account, admin)
   - Collateral types (USDC, wETH, wstETH, wBTC)
   - Margin types (standard_margin, portfolio_margin)

2. **session.py** - HTTP session with rate limiting
   - Thread-safe SQLite-backed rate limiting
   - Exponential backoff retry logic
   - Connection pooling
   - Based on Hyperliquid pattern

3. **authentication.py** - Session key authentication
   - `DeriveApiClient` class for API interactions
   - EIP-712 signature-based session key registration
   - Authenticated JSON-RPC 2.0 requests
   - Request signing with session keys

4. **account.py** - Account balance reading
   - `CollateralBalance` dataclass
   - `AccountSummary` dataclass
   - `fetch_account_collaterals()` function
   - `fetch_account_summary()` function

5. **__init__.py** - Module documentation
   - Comprehensive examples
   - Authentication workflow
   - Environment variable documentation

### Tests (`tests/derive/`)

1. **conftest.py** - Test fixtures
   - Owner account fixture
   - Derive client fixture
   - Session key client fixture (auto-registers if needed)
   - Test account wallet fixture

2. **test_authentication.py** - Authentication tests
   - Session key registration test
   - Validation tests for required parameters

3. **test_account_balance.py** - Balance reading tests
   - Collateral fetching test
   - Account summary test
   - Authentication requirement test

### Documentation (`docs/source/api/derive/`)

1. **index.rst** - Complete API documentation
   - Preface explaining Derive.xyz
   - Authentication section with three-tier wallet system
   - Getting started examples
   - Environment variables guide
   - Test account creation instructions
   - Links to all resources

2. Updated **docs/source/api/index.rst** to include Derive

### Configuration

1. **.env.derive.example** - Environment variable template
   - Complete setup instructions
   - Variable descriptions
   - Testing commands

2. **CHANGELOG.md** - Updated with new feature

## Environment Variables Required

```bash
# Owner wallet (signs session key registrations)
DERIVE_OWNER_PRIVATE_KEY=0x...

# Derive smart contract wallet (NOT your EOA)
DERIVE_WALLET_ADDRESS=0x...

# Session key (optional, auto-generated if missing)
DERIVE_SESSION_KEY_PRIVATE=0x...

# Enable real API calls
SEND_REAL_REQUESTS=true
```

## How to Use

### Basic Example

```python
from eth_account import Account
from eth_defi.derive.authentication import DeriveApiClient, SessionKeyScope
from eth_defi.derive.account import fetch_account_summary

# Initialize client
owner = Account.from_key("0x...")
client = DeriveApiClient(
    owner_account=owner,
    derive_wallet_address="0x...",  # From Derive.xyz interface
    is_testnet=True,
)

# Register session key
session = client.register_session_key(
    scope=SessionKeyScope.read_only,
    expiry_hours=24,
)
client.session_key_private = session["session_key_private"]

# Fetch balances
summary = fetch_account_summary(client)
print(f"Total: ${summary.total_value_usd}")
for col in summary.collaterals:
    print(f"{col.token}: {col.available}")
```

### Running Tests

```bash
# Setup environment variables
export DERIVE_OWNER_PRIVATE_KEY=0x...
export DERIVE_WALLET_ADDRESS=0x...
export SEND_REAL_REQUESTS=true

# Run all tests
source .local-test.env && poetry run pytest tests/derive/ -v

# Run with logging
source .local-test.env && poetry run pytest tests/derive/ -v --log-cli-level=info
```

## Key Design Decisions

1. **Authentication Pattern**: Followed Orderly API client pattern for EIP-712 signing and session key management

2. **Session Management**: Reused Hyperliquid's proven session.py with SQLite-backed rate limiting for thread-safety

3. **Data Structures**: Used `@dataclass(slots=True)` with `Decimal` for financial values following codebase standards

4. **Error Handling**: Clear error messages guide users to set up authentication properly

5. **Testing Strategy**: Tests skip gracefully when environment variables not set, auto-register session keys if needed

## Authentication Flow

1. **Owner EOA** → Your original Ethereum wallet
2. **Derive Wallet** → Smart contract wallet on Derive Chain (get from Derive.xyz interface)
3. **Session Key** → Temporary wallet registered by owner for API access

Session keys support three permission levels:
- `read_only` - View account data
- `account` - Manage orders and settings
- `admin` - Full trading and withdrawal access

## Testing Account Creation

To create a test account on Derive testnet:

1. Generate new wallet: `Account.create()`
2. Fund with Sepolia ETH (gas)
3. Visit https://testnet.derive.xyz/ and connect
4. Mint USDC via interface or Circle faucet
5. Find Derive Wallet address in interface (Developers section)
6. Register session key via API

## Files Created

**Module files:**
- `eth_defi/derive/__init__.py` (1,973 bytes)
- `eth_defi/derive/constants.py` (2,126 bytes)
- `eth_defi/derive/session.py` (3,503 bytes)
- `eth_defi/derive/authentication.py` (10,581 bytes)
- `eth_defi/derive/account.py` (6,856 bytes)

**Test files:**
- `tests/derive/conftest.py` (2,657 bytes)
- `tests/derive/test_authentication.py` (2,132 bytes)
- `tests/derive/test_account_balance.py` (3,129 bytes)

**Documentation:**
- `docs/source/api/derive/index.rst` (3,618 bytes)
- `.env.derive.example` (2,168 bytes)
- Updated `docs/source/api/index.rst`
- Updated `CHANGELOG.md`

## Code Quality

- ✅ Formatted with ruff
- ✅ Type hints throughout
- ✅ British English in documentation
- ✅ Sphinx-compatible docstrings
- ✅ Follows all codebase conventions
- ✅ Comprehensive error handling

## External Resources

- [Derive.xyz Platform](https://www.derive.xyz/)
- [Testnet Interface](https://testnet.derive.xyz/)
- [API Documentation](https://docs.derive.xyz/)
- [Session Keys Guide](https://docs.derive.xyz/reference/session-keys)
- [Python Signing SDK](https://github.com/derivexyz/v2-action-signing-python)
- [Derive Chain Explorer](https://explorer.derive.xyz/)
- [Circle USDC Faucet](https://faucet.circle.com/)

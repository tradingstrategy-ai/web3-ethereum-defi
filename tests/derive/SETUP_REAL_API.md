# Setting Up Real Derive.xyz API Credentials for Testing

## Overview

To test with real Derive.xyz API, you need:
1. A wallet with some Sepolia ETH (for gas)
2. A Derive Wallet (created via Derive.xyz interface)
3. API credentials registered with Derive

## Step-by-Step Setup

### Step 1: Create or Use Existing Wallet

```python
from eth_account import Account

# Option A: Create new wallet
wallet = Account.create()
print(f"Address: {wallet.address}")
print(f"Private Key: {wallet.key.hex()}")

# Option B: Use existing wallet
wallet = Account.from_key("0x...")
```

### Step 2: Fund Wallet with Sepolia ETH

Get Sepolia ETH from faucet:
- https://sepoliafaucet.com/
- Minimum: 0.01 ETH (for gas fees)

### Step 3: Create Derive Wallet

1. Visit https://testnet.derive.xyz/
2. Connect your wallet (MetaMask or WalletConnect)
3. The interface will automatically create a Derive Wallet (smart contract)
4. Go to: Home → Developers → "Derive Wallet"
5. Copy the Derive Wallet address (starts with 0x)

**Important:** This is NOT your EOA address - it's a smart contract wallet created by Derive.

### Step 4: Configure Environment Variables

Add to your `.local-test.env` file:

```bash
# Your wallet private key (the one you funded with Sepolia ETH)
export DERIVE_OWNER_PRIVATE_KEY=0x...

# The Derive Wallet address (from step 3)
export DERIVE_WALLET_ADDRESS=0x...

# Enable real API calls
export SEND_REAL_REQUESTS=true
```

### Step 5: Run Tests

The tests will automatically:
1. Use your owner wallet to register a session key
2. Use the session key to authenticate API requests
3. Read your account balance (which will be empty if you haven't deposited)

```bash
source .local-test.env
poetry run pytest tests/derive/test_real_api.py -v --log-cli-level=info
```

## Testing Empty Account

Your account will be empty by default (no collateral deposited). The test will verify:
- Empty accounts return `[]` for collaterals
- API doesn't error with empty accounts
- Account summary shows $0 total value

## Optional: Add Small Balance

If you want to test with actual balance:

1. Get testnet USDC from Circle faucet: https://faucet.circle.com/
2. Go to https://testnet.derive.xyz/
3. Deposit some USDC to your Derive account
4. Re-run tests to see collateral data

## Troubleshooting

### Error: "owner_account is required"
- Make sure `DERIVE_OWNER_PRIVATE_KEY` is set correctly

### Error: "derive_wallet_address required"
- Make sure `DERIVE_WALLET_ADDRESS` is set correctly
- This must be the Derive Wallet (from interface), not your EOA

### Error: Network/API errors
- Verify you have internet connection
- Check Derive API is accessible: https://api-demo.lyra.finance/
- Try again in a few minutes (API may be rate limiting)

### Session Key Persists
First test run will register a session key and print it:
```
Generated session key: 0x...
Save as DERIVE_SESSION_KEY_PRIVATE for future tests
```

Save this to avoid re-registering every time:
```bash
export DERIVE_SESSION_KEY_PRIVATE=0x...
```

## Cost

- **Gas Fees**: ~0.001 ETH per session key registration
- **API Calls**: Free (read-only)
- **USDC Deposit**: Optional (only if testing with balance)

Total cost to test: ~$0 (just Sepolia testnet ETH, which is free)

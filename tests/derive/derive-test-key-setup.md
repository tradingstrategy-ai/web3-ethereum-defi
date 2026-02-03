# Setting up Derive.xyz API credentials for testing

## Prerequisites

1. A wallet (any Ethereum wallet with a private key)
2. A Derive account created via the web interface

## Step 1: Create Derive account

1. Visit https://testnet.derive.xyz/
2. Connect your wallet (MetaMask or WalletConnect)
3. The interface will deploy your LightAccount (ERC-4337 smart contract wallet)

**Important:** The Derive Wallet is NOT your EOA address -- it is a
LightAccount smart contract wallet deployed on Derive Chain.

## Step 2: Get credentials from developer page

1. Go to: Home → Developers
2. Copy the **Derive Wallet** address (starts with 0x)
3. Copy the **Session Key** private key

## Step 3: Export your wallet private key

`DERIVE_OWNER_PRIVATE_KEY` is the private key of the Ethereum wallet you
connected to the Derive web interface in Step 1.

**MetaMask:**

1. Open MetaMask → click the three-dot menu on your account
2. Select "Account details"
3. Click "Show private key"
4. Enter your MetaMask password to reveal the key
5. Copy the hex string (starts with 0x)

**Rabby:**

1. Open Rabby → click your account address at the top
2. Click the three-dot menu → "Export Private Key"
3. Enter your Rabby password
4. Copy the hex string

**Other wallets:** Look for "Export private key" or "Show private key"
in your wallet's account/security settings.

## Step 4: Configure environment

Add to your `.local-test.env` file:

```bash
# Your wallet private key (the one used to connect via web UI)
export DERIVE_OWNER_PRIVATE_KEY=0x...

# Session key private key (from testnet developer page)
export DERIVE_SESSION_PRIVATE_KEY=0x...

# The Derive Wallet address (from testnet developer page)
export DERIVE_WALLET_ADDRESS=0x...
```

## Step 5: Run tests

```bash
source .local-test.env && poetry run pytest tests/derive/ -v --log-cli-level=info
```

The tests will:
1. Resolve the LightAccount address from your owner key
2. Authenticate using the session key
3. Read account collateral and summary data

## Troubleshooting

### Error: "Account not found"
Your account does not exist in Derive's backend. Create it via the web
interface first (https://testnet.derive.xyz/).

### Error: 403 Forbidden from nginx
The session key is not recognised for this wallet. Check that:
- The session key was created for this specific wallet
- The session key has not expired
- You copied the full private key (including 0x prefix)

### Tests skip with "Set DERIVE_OWNER_PRIVATE_KEY and DERIVE_SESSION_PRIVATE_KEY"
The environment variables are not set. Check your `.local-test.env` file.

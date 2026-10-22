# Derive.xyz integration -- implementation summary

## Overview

Funding history and instrument discovery use Derive's public v3 API at
`https://api.derive.xyz/v3`, following the [migration guide](https://docs.derive.xyz/migrating/breaking-changes).
V3 settles on Ethereum. Historical open-interest snapshots still read legacy
Derive Chain state; existing v2 funding rows are preserved because the v3
endpoint does not currently expose the full older history.

The authentication and onboarding modules below describe the legacy v2
EIP-191 session keys and LightAccount wallets. They have not been migrated
to v3; changing the public-data defaults does not change their endpoints.

## Architecture

The legacy v2 integration uses a three-tier wallet system:

1. **Owner EOA** -- original Ethereum wallet
2. **Derive Wallet** -- LightAccount smart contract wallet on Derive Chain (ERC-4337)
3. **Session Keys** -- temporary wallets for API access

Account creation and initial session key registration require the Derive web interface due to:
- ERC-4337 UserOperation gas sponsorship via paymaster
- SIWE (Sign-In with Ethereum) bot detection on the verify endpoint
- No public API endpoint for account creation

## Modules

- `constants.py` -- API URLs, chain IDs, contract addresses, EIP-712 constants, enums
- `session.py` -- HTTP session with SQLite-backed rate limiting
- `api.py` -- v3 public funding/instrument reads and legacy onchain snapshots
- `historical.py` -- persistent funding history and legacy open-interest storage
- `v3_vaults.py` -- public v3 native vault collection
- `authentication.py` -- DeriveApiClient with EIP-191 personal-sign auth
- `account.py` -- balance and collateral reading functions
- `onboarding.py` -- LightAccount address resolution and session key verification

## Authentication

Uses EIP-191 personal-sign (`encode_defunct(text=timestamp)`) with uppercase headers:

- `X-LYRAWALLET` -- LightAccount smart contract wallet address
- `X-LYRATIMESTAMP` -- UTC timestamp in milliseconds
- `X-LYRASIGNATURE` -- signature of the timestamp string

Matches the `derive_action_signing` package format (v0.0.13).

## Contract addresses (testnet)

- LightAccountFactory: `0x000000893A26168158fbeaDD9335Be5bC96592E2`
- EntryPoint: `0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789`
- Matching: `0x3cc154e220c2197c5337b7Bd13363DD127Bc0C6E`
- Standard Risk Manager: `0x28bE681F7bEa6f465cbcA1D25A2125fe7533391C`
- Deposit Module: `0x43223Db33AdA0575D2E100829543f8B04A37a1ec`
- Bundler: `https://bundler-prod-testnet-0eakp60405.t.conduit.xyz`

## Testing

```bash
# Owner wallet private key (from web UI wallet, e.g. MetaMask export)
export DERIVE_OWNER_PRIVATE_KEY=0x...

# Session key private key (from Derive developer page: Home → Developers)
export DERIVE_SESSION_PRIVATE_KEY=0x...

# Optional: Derive wallet address (auto-derived from owner key if not set)
export DERIVE_WALLET_ADDRESS=0x...

# Run tests
source .local-test.env && poetry run pytest tests/derive/ -v --log-cli-level=info
```

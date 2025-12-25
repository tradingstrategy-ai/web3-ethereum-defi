# GMX Forked env example - Quick Start

Standalone fork tests for GMX Synthetics V2 on Arbitrum. Describes the steps to open/close positions using real mainnet contracts.

**Self-contained**: Copy this directory anywhere and follow the setup below.

## Setup (2 minutes)

```bash
cd forked-env-example

# Initialize git (required for forge install if setting up as a stand alone repo)
git init

# Install dependencies (just forge-std)
forge install foundry-rs/forge-std --no-commit

# Set Arbitrum RPC URL
cp .env.example .env && source .env
```

## Run Tests

```bash
forge test --fork-url $ARBITRUM_RPC_URL -vv
forge test --fork-url $ARBITRUM_RPC_URL --match-test testOpenLongPosition -vv
forge test --fork-url $ARBITRUM_RPC_URL --match-test testCloseLongPosition -vv
```

## What This Does

Tests demonstrate the GMX order flow:

1. **Create order** - User sends collateral + execution fee to GMX
2. **Execute order** - Keeper executes with oracle prices (mocked in these tests)
3. **Verify position** - Check position was created/closed correctly

Example: `testOpenLongPosition` opens a 2.5x leveraged long ETH position with 0.001 ETH collateral (~$3.89 → ~$9.7 position).

## How It Works

**Fork testing**: Tests run against real GMX contracts on Arbitrum mainnet at block 392496384 (matches a real transaction).

**Oracle mocking**: GMX uses Chainlink Data Streams (off-chain signed prices). Oracle provider bytecode is replaced with a mock using **`vm.etch`** so orders can be executed without real signatures.

**Key files**:
- `contracts/constants/GmxArbitrumAddresses.sol` - Production contract addresses (all arbitrum deployments [here](https://github.com/gmx-io/gmx-synthetics/blob/main/docs/arbitrum-deployments.md))
- `contracts/mocks/MockOracleProvider.sol` - Oracle price mocking. Critical step to bypass the Chainlink Data Stream signature verification on a forked env
- `contracts/interfaces/IGmxV2.sol` - Minimal GMX interfaces. Miminal code copied from the GMX contracts/interfaces.
- `contracts/utils/GmxForkHelpers.sol` - Reusable helpers for order creation, state queries
- `test/GmxOrderFlow.t.sol` - Main test contract

## What You'll Learn

- How to create GMX orders (MarketIncrease, MarketDecrease)
- Two-step execution model (user creates → keeper executes)
- Handling oracle prices and execution fees
- Querying positions and verifying state changes

**Oracle provider address** mocked `0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD` (Chainlink Data Stream provider verified from mainnet txs).

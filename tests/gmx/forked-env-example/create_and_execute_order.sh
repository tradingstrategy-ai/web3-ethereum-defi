#!/bin/bash
set -e

# Complete GMX Order Flow on Tenderly
#
# This script:
# 0. Funds user account using tenderly_setBalance
# 1. Deploys MockOracleProvider and sets it up (shows on Tenderly)
# 2. Creates GMX order (shows on Tenderly)
# 3. Executes order as keeper (shows on Tenderly)
#
# Usage:
#   ./create_and_execute_order.sh <tenderly_rpc_url>
#
# Example:
#   ./create_and_execute_order.sh https://virtual.arbitrum.eu.rpc.tenderly.co/5f46640f-de2f-4e02-b731-32174172f5cf

TENDERLY_RPC=$1

if [ -z "$TENDERLY_RPC" ]; then
    echo "Usage: $0 <tenderly_rpc_url>"
    echo ""
    echo "Example:"
    echo "  $0 https://virtual.arbitrum.eu.rpc.tenderly.co/5f46640f..."
    exit 1
fi

# GMX addresses
ORDER_HANDLER="0x04315E233C1c6FfA61080B76E29d5e8a1f7B4A35"
KEEPER="0xE47b36382DC50b90bCF6176Ddb159C4b9333A7AB"
ORACLE_PROVIDER="0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD"
WETH="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC="0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

echo "╔════════════════════════════════════════════════════════════╗"
echo "║  Complete GMX Order Flow on Tenderly                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# STEP 0: Fund Accounts
# =============================================================================
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│ STEP 0: Fund Accounts                                       │"
echo "└─────────────────────────────────────────────────────────────┘"

# Get user address from private key
USER_ADDRESS=$(cast wallet address --private-key $PRIVATE_KEY)

echo "→ Funding user account: $USER_ADDRESS"
FUND_BALANCE="0xDE0B6B3A7640000"  # 1 ETH in hex

curl -s -X POST $TENDERLY_RPC \
    -H "Content-Type: application/json" \
    -d '{
        "jsonrpc": "2.0",
        "method": "tenderly_setBalance",
        "params": ["'"$USER_ADDRESS"'", "'"$FUND_BALANCE"'"],
        "id": 1
    }' > /dev/null

echo "✓ User account funded with 1 ETH"
echo ""

# =============================================================================
# STEP 1: Deploy and Setup Mock Oracle
# =============================================================================
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│ STEP 1: Deploy & Setup Mock Oracle                          │"
echo "└─────────────────────────────────────────────────────────────┘"

echo "→ Getting MockOracleProvider bytecode..."
BYTECODE=$(forge inspect contracts/mock/MockOracleProvider.sol:MockOracleProvider bytecode 2>/dev/null)

if [ -z "$BYTECODE" ]; then
    echo "✗ Error: Failed to get bytecode"
    exit 1
fi

echo "✓ Got bytecode (${#BYTECODE} chars)"
echo ""

echo "→ Deploying MockOracleProvider..."
DEPLOY_TX=$(cast send \
    --rpc-url $TENDERLY_RPC \
    --private-key $PRIVATE_KEY \
    --create "$BYTECODE" \
    --json)

MOCK_ADDRESS=$(echo "$DEPLOY_TX" | jq -r '.contractAddress')

if [ -z "$MOCK_ADDRESS" ] || [ "$MOCK_ADDRESS" = "null" ]; then
    echo "✗ Error: Deployment failed"
    exit 1
fi

echo "✓ MockOracleProvider deployed: $MOCK_ADDRESS"
echo ""

echo "→ Setting prices on mock oracle..."
# GMX price format: price * 10^30 / 10^tokenDecimals
WETH_PRICE="3343923406460000"      # 3492 * 10^12 (18 decimals)
USDC_PRICE="1000000000000000000000000"  # 1 * 10^24 (6 decimals)

cast send $MOCK_ADDRESS \
    "setPrice(address,uint256,uint256)" \
    $WETH \
    $WETH_PRICE \
    $WETH_PRICE \
    --rpc-url $TENDERLY_RPC \
    --private-key $PRIVATE_KEY > /dev/null

cast send $MOCK_ADDRESS \
    "setPrice(address,uint256,uint256)" \
    $USDC \
    $USDC_PRICE \
    $USDC_PRICE \
    --rpc-url $TENDERLY_RPC \
    --private-key $PRIVATE_KEY > /dev/null

echo "✓ Prices set (WETH: $WETH_PRICE, USDC: \$1)"
echo ""

echo "→ Replacing oracle bytecode via tenderly_setCode..."
MOCK_CODE=$(cast code $MOCK_ADDRESS --rpc-url $TENDERLY_RPC)

SETCODE_RESULT=$(curl -s -X POST $TENDERLY_RPC \
    -H "Content-Type: application/json" \
    -d '{
        "jsonrpc": "2.0",
        "method": "tenderly_setCode",
        "params": [
            "'"$ORACLE_PROVIDER"'",
            "'"$MOCK_CODE"'"
        ],
        "id": 1
    }')

echo "✓ Oracle bytecode replaced at: $ORACLE_PROVIDER"
echo ""

# =============================================================================
# STEP 2: Create GMX Order
# =============================================================================
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│ STEP 2: Create GMX Order                                    │"
echo "└─────────────────────────────────────────────────────────────┘"

echo "→ Running CreateGmxOrder.s.sol..."
echo ""

CREATE_OUTPUT=$(forge script script/CreateGmxOrder.s.sol \
    --rpc-url $TENDERLY_RPC \
    --broadcast \
    -vv 2>&1)

# Extract order key from output
ORDER_KEY=$(echo "$CREATE_OUTPUT" | grep "Order created. Order key:" | sed 's/.*Order key: //')

if [ -z "$ORDER_KEY" ]; then
    echo "✗ Error: Failed to create order"
    echo "$CREATE_OUTPUT" | tail -20
    exit 1
fi

echo "$CREATE_OUTPUT" | grep -A 20 "Order details:"
echo ""
echo "✓ Order created: $ORDER_KEY"
echo ""

# =============================================================================
# STEP 3: Execute Order as Keeper
# =============================================================================
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│ STEP 3: Execute Order as Keeper                             │"
echo "└─────────────────────────────────────────────────────────────┘"

echo "→ Executing order as keeper: $KEEPER"
echo ""

EXEC_RESULT=$(cast send $ORDER_HANDLER \
    "executeOrder(bytes32,(address[],address[],bytes[]))" \
    $ORDER_KEY \
    "([${WETH},${USDC}],[${ORACLE_PROVIDER},${ORACLE_PROVIDER}],[0x,0x])" \
    --rpc-url $TENDERLY_RPC \
    --unlocked \
    --from $KEEPER \
    --gas-limit 5000000 \
    --json)

EXEC_TX=$(echo "$EXEC_RESULT" | jq -r '.transactionHash')
EXEC_STATUS=$(echo "$EXEC_RESULT" | jq -r '.status')

if [ "$EXEC_STATUS" = "0x1" ]; then
    echo "✓ Order executed successfully!"
else
    echo "✗ Order execution failed (but trace is on Tenderly)"
fi

echo ""

# =============================================================================
# Summary
# =============================================================================
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                      SUCCESS                               ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "Tenderly Transactions:"
echo "  0. Fund user account (tenderly_setBalance)"
echo "  1. Deploy MockOracleProvider"
echo "  2. Set WETH price"
echo "  3. Set USDC price"
echo "  4. ExchangeRouter.sendWnt()"
echo "  5. ExchangeRouter.createOrder()"
echo "  6. OrderHandler.executeOrder() ← Full trace here!"
echo ""
echo "Order Key:  $ORDER_KEY"
echo "Execute TX: $EXEC_TX"
echo ""
echo "Check Tenderly dashboard for complete execution trace!"

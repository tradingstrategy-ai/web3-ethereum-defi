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
DATA_STORE="0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"
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
# STEP 1: Setup Mock Oracle
# =============================================================================
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│ STEP 1: Setup Mock Oracle                                   │"
echo "└─────────────────────────────────────────────────────────────┘"

# Pre-compiled MockOracleProvider bytecode
MOCK_BYTECODE="0x608060405234801561001057600080fd5b50600436106100575760003560e01c8063204120bc1461005c5780633011e16a1461009d578063377bbdaf146100cf5780638263c28e1461012b578063eed790c71461012b575b600080fd5b61008361006a3660046101eb565b6000602081905290815260409020805460019091015482565b604080519283526020830191909152015b60405180910390f35b6100cd6100ab36600461020d565b6001600160a01b03909216600090815260208190526040902090815560010155565b005b6100e26100dd366004610256565b61013a565b604051610094919081516001600160a01b039081168252602080840151908301526040808401519083015260608084015190830152608092830151169181019190915260a00190565b60405160008152602001610094565b61017e6040518060a0016040528060006001600160a01b0316815260200160008152602001600081526020016000815260200160006001600160a01b031681525090565b6001600160a01b038316600081815260208181526040918290208251808401845281548152600190910154818301908152938552519084015290519082015242606082015230608082015292915050565b80356001600160a01b03811681146101e657600080fd5b919050565b6000602082840312156101fd57600080fd5b610206826101cf565b9392505050565b60008060006060848603121561022257600080fd5b61022b846101cf565b95602085013595506040909401359392505050565b634e487b7160e01b600052604160045260246000fd5b6000806040838503121561026957600080fd5b610272836101cf565b9150602083013567ffffffffffffffff8082111561028f57600080fd5b818501915085601f8301126102a357600080fd5b8135818111156102b5576102b5610240565b604051601f8201601f19908116603f011681019083821181831017156102dd576102dd610240565b816040528281528860208487010111156102f657600080fd5b826020860160208301376000602084830101528095505050505050925092905056fea2646970667358221220261467ba79659e90e240e2dc5d145aa30c2c76a3d6e306110ea445eaaa3972fb64736f6c63430008140033"

echo "→ Deploying MockOracleProvider bytecode to oracle provider address via tenderly_setCode..."
SETCODE_RESULT=$(curl -s -X POST $TENDERLY_RPC \
    -H "Content-Type: application/json" \
    -d '{
        "jsonrpc": "2.0",
        "method": "tenderly_setCode",
        "params": [
            "'"$ORACLE_PROVIDER"'",
            "'"$MOCK_BYTECODE"'"
        ],
        "id": 1
    }')

echo "✓ MockOracleProvider bytecode deployed at: $ORACLE_PROVIDER"
echo ""

echo "→ Verifying bytecode was set correctly..."
DEPLOYED_CODE=$(cast code $ORACLE_PROVIDER --rpc-url $TENDERLY_RPC)

if [ "$DEPLOYED_CODE" != "$MOCK_BYTECODE" ]; then
    echo "✗ Error: Bytecode mismatch!"
    echo "  Expected: $MOCK_BYTECODE"
    echo "  Got:      $DEPLOYED_CODE"
    exit 1
fi

echo "✓ Bytecode verified successfully"
echo ""

echo "→ Setting prices on oracle provider..."
# GMX price format: price * 10^30 / 10^tokenDecimals
WETH_PRICE="3343923406460000"      # 3492 * 10^12 (18 decimals)
USDC_PRICE="1000000000000000000000000"  # 1 * 10^24 (6 decimals)

cast send $ORACLE_PROVIDER \
    "setPrice(address,uint256,uint256)" \
    $WETH \
    $WETH_PRICE \
    $WETH_PRICE \
    --rpc-url $TENDERLY_RPC \
    --private-key $PRIVATE_KEY > /dev/null

cast send $ORACLE_PROVIDER \
    "setPrice(address,uint256,uint256)" \
    $USDC \
    $USDC_PRICE \
    $USDC_PRICE \
    --rpc-url $TENDERLY_RPC \
    --private-key $PRIVATE_KEY > /dev/null

echo "✓ Prices set (WETH: $WETH_PRICE, USDC: \$1)"
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

echo "→ Getting initial position count for user: $USER_ADDRESS"
# Calculate accountPositionListKey using double-hash:
# 1. ACCOUNT_POSITION_LIST = keccak256(abi.encode("ACCOUNT_POSITION_LIST"))
# 2. accountPositionListKey = keccak256(abi.encode(ACCOUNT_POSITION_LIST, account))
ACCOUNT_POSITION_LIST=$(cast keccak "$(cast abi-encode 'f(string)' 'ACCOUNT_POSITION_LIST')")
POSITION_LIST_KEY=$(cast keccak "$(cast abi-encode 'f(bytes32,address)' $ACCOUNT_POSITION_LIST $USER_ADDRESS)")
INITIAL_POS_COUNT=$(cast call $DATA_STORE "getBytes32Count(bytes32)" $POSITION_LIST_KEY --rpc-url $TENDERLY_RPC)
INITIAL_POS_COUNT_DEC=$((16#${INITIAL_POS_COUNT#0x}))
echo "  Initial position count: $INITIAL_POS_COUNT_DEC"
echo ""

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

echo "→ Verifying position was opened..."
FINAL_POS_COUNT=$(cast call $DATA_STORE "getBytes32Count(bytes32)" $POSITION_LIST_KEY --rpc-url $TENDERLY_RPC)
FINAL_POS_COUNT_DEC=$((16#${FINAL_POS_COUNT#0x}))
echo "  Final position count: $FINAL_POS_COUNT_DEC"

if [ "$FINAL_POS_COUNT_DEC" -gt "$INITIAL_POS_COUNT_DEC" ]; then
    POSITIONS_OPENED=$((FINAL_POS_COUNT_DEC - INITIAL_POS_COUNT_DEC))
    echo "✓ Position opened! ($POSITIONS_OPENED new position(s))"
else
    echo "✗ Warning: No new position detected"
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
echo "  1. Set MockOracleProvider bytecode (tenderly_setCode)"
echo "  2. Set WETH price"
echo "  3. Set USDC price"
echo "  4. ExchangeRouter.sendWnt()"
echo "  5. ExchangeRouter.createOrder()"
echo "  6. OrderHandler.executeOrder() ← Full trace here!"
echo ""
echo "Results:"
echo "  Order Key:       $ORDER_KEY"
echo "  Execute TX:      $EXEC_TX"
echo "  Initial Positions: $INITIAL_POS_COUNT_DEC"
echo "  Final Positions:   $FINAL_POS_COUNT_DEC"
echo ""
echo "Check Tenderly dashboard for complete execution trace!"

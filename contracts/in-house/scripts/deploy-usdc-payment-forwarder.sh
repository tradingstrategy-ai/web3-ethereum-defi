#!/bin/bash

set -e
set -x
set -u

forge create --rpc-url $JSON_RPC_POLYGON \
    --constructor-args $USDC_TOKEN $VAULT_COMPTROLLER \
    --private-key $PRIVATE_KEY \
    --etherscan-api-key $POLYGONSCAN_API_KEY \
    src/VaultUSDCPaymentForwarder.sol:VaultUSDCPaymentForwarder


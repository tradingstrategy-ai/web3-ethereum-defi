  #!/bin/bash

set -e
set -x
set -u

forge verify-contract \
    --etherscan-api-key $POLYGONSCAN_API_KEY \
    --flatten \
    --force \
    --chain polygon \
    --constructor-args $(cast abi-encode "constructor(address,address)" $USDC_TOKEN $VAULT_COMPTROLLER) \
     $CONTRACT_ADDRESS \
     src/VaultUSDCPaymentForwarder.sol:VaultUSDCPaymentForwarder


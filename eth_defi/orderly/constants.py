"""Constants for Orderly Protocol EIP-712 typed data messages."""

#: EIP-712 message types for Orderly Protocol operations so we can setup a trading account
MESSAGE_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Registration": [
        {"name": "brokerId", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "timestamp", "type": "uint64"},
        {"name": "registrationNonce", "type": "uint256"},
    ],
    "AddOrderlyKey": [
        {"name": "brokerId", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "orderlyKey", "type": "string"},
        {"name": "scope", "type": "string"},
        {"name": "timestamp", "type": "uint64"},
        {"name": "expiration", "type": "uint64"},
    ],
    "Withdraw": [
        {"name": "brokerId", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "receiver", "type": "address"},
        {"name": "token", "type": "string"},
        {"name": "amount", "type": "uint256"},
        {"name": "withdrawNonce", "type": "uint64"},
        {"name": "timestamp", "type": "uint64"},
    ],
    "SettlePnl": [
        {"name": "brokerId", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "settleNonce", "type": "uint64"},
        {"name": "timestamp", "type": "uint64"},
    ],
    "DelegateSigner": [
        {"name": "delegateContract", "type": "address"},
        {"name": "brokerId", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "timestamp", "type": "uint64"},
        {"name": "registrationNonce", "type": "uint256"},
        {"name": "txHash", "type": "bytes32"},
    ],
    "DelegateAddOrderlyKey": [
        {"name": "delegateContract", "type": "address"},
        {"name": "brokerId", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "orderlyKey", "type": "string"},
        {"name": "scope", "type": "string"},
        {"name": "timestamp", "type": "uint64"},
        {"name": "expiration", "type": "uint64"},
    ],
    "DelegateWithdraw": [
        {"name": "delegateContract", "type": "address"},
        {"name": "brokerId", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "receiver", "type": "address"},
        {"name": "token", "type": "string"},
        {"name": "amount", "type": "uint256"},
        {"name": "withdrawNonce", "type": "uint64"},
        {"name": "timestamp", "type": "uint64"},
    ],
    "DelegateSettlePnl": [
        {"name": "delegateContract", "type": "address"},
        {"name": "brokerId", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "settleNonce", "type": "uint64"},
        {"name": "timestamp", "type": "uint64"},
    ],
}

"""
GMX Constants Module

This module provides contract addresses and ABIs for the GMX protocol.
"""

from pathlib import Path
import json

# Define the base path relative to this script
base_dir = Path(__file__).resolve().parent

GMX_API_URLS: dict = {"arbitrum": "https://arbitrum-api.gmxinfra.io", "avalanche": "https://avalanche-api.gmxinfra.io"}

GMX_API_URLS_BACKUP: dict = {"arbitrum": "https://arbitrum-api.gmxinfra2.io", "avalanche": "https://avalanche-api.gmxinfra2.io"}

# Contract addresses by chain
GMX_EVENT_EMITTER_ADDRESS = {"arbitrum": "0xC8ee91A54287DB53897056e12D9819156D3822Fb", "avalanche": "0xDb17B211c34240B014ab6d61d4A31FA0C0e20c26"}

GMX_DATASTORE_ADDRESS = {"arbitrum": "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8", "avalanche": "0x2F0b22339414ADeD7D5F06f9D604c7fF5b2fe3f6"}

GMX_READER_ADDRESS = {"arbitrum": "0x5Ca84c34a381434786738735265b9f3FD814b824", "avalanche": "0xBAD04dDcc5CC284A86493aFA75D2BEb970C72216"}

GMX_EXCHANGE_ROUTER_ADDRESS = {"arbitrum": "0x900173A66dbD345006C51fA35fA3aB760FcD843b", "avalanche": "0x2b76df209E1343da5698AF0f8757f6170162e78b"}

# Token addresses by chain
GMX_TOKEN_ADDRESSES = {"arbitrum": {"ETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "BTC": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "USDC": "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8", "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "LINK": "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4", "UNI": "0xFa7F8980b0f1E64A2062791cc3b0871572f1F7f0"}, "avalanche": {}}


# Define the paths to ABI files
abi_paths = {"arbitrum": base_dir / "../" / "abi" / "gmx" / "arbitrum" / "eventemitter.json", "avalanche": base_dir / "../" / "abi" / "gmx" / "avalanche" / "eventemitter.json"}

# Read and parse the JSON ABI files
GMX_EVENT_EMITTER_ABI = {chain: json.loads(path.read_text()) for chain, path in abi_paths.items()}


# Event signatures for GMX contracts
EVENT_SIGNATURES = {
    "UpdateFundingRate": "0xaa58a1c124fe8c67db114d6a19c3ef5b564f4ef3bd820f71e94473e846e3bb12",
    "IncreasePosition": "0x2fe68525253654c21998f35787a8d0f361bd444120e6c65920e8f7e9e4c26930",
    "DecreasePosition": "0xca28a6b76a3f6dc9124d60540e577c6adbd1e3ba0b52e013908b9ad5f15a4464",
    "LiquidatePosition": "0x2e1f85a5194ea85aa10539a6e819c82b7244e0a61ab25bd09627a29e2f7b996b",
    "SetPrice": "0x42b65f4eb3437d54b4e320a5863c8a1c28e539af1226161b7602ef73f567da5c",
}


# Key GMX event signatures
GMX_EVENT_SIGNATURES = {
    "PositionIncrease": "PositionIncrease(bytes32,address,address,address,uint256,uint256,uint256,uint256,uint256,uint256)",
    "PositionDecrease": "PositionDecrease(bytes32,address,address,address,uint256,uint256,uint256,uint256,uint256,uint256)",
    "PositionLiquidated": "PositionLiquidated(bytes32,address,address,address,bool,uint256,uint256,uint256,uint256,uint256,uint256)",
    "SwapExecuted": "SwapExecuted(address,address,address,uint256,uint256,uint256,uint256)",
    "DepositExecuted": "DepositExecuted(bytes32,address,address,address,uint256,uint256,uint256)",
    "WithdrawalExecuted": "WithdrawalExecuted(bytes32,address,address,address,uint256,uint256,uint256)",
    "OrderExecuted": "OrderExecuted(bytes32,address,address,address,uint256,uint256,uint256,uint256,uint256,uint256,uint256)",
    "OrderCancelled": "OrderCancelled(bytes32,address,address,address,uint256,uint256,uint256,uint256,uint256,uint256,uint256)",
    "FundingUpdated": "FundingUpdated(address,uint256,uint256)",
    "CollateralClaimAction": "CollateralClaimAction(address,address,address,address,uint256)",
}

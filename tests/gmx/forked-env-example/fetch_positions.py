#!/usr/bin/env python3
"""
Fetch and display GMX open positions for an account.

Usage:
    python fetch_positions.py

Or with custom RPC:
    python fetch_positions.py <rpc_url> <account_address>
"""

import sys
from web3 import Web3
from eth_abi import encode


# Configuration
DEFAULT_RPC_URL = "https://virtual.arbitrum.eu.rpc.tenderly.co/add2d7e4-4957-47a0-92f7-73fa23189bd0"
# 0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c
DEFAULT_ACCOUNT = "0x6DC51f9C50735658Cc6a003e07B0b92dF9c98473"

# GMX Contract Addresses (Arbitrum)
DATA_STORE = "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"
READER = "0x65A6CC451BAfF7e7B4FDAb4157763aB4b6b44D0E"

# Market info for display
MARKETS = {
    "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336": "ETH/USD",
    "0x47c031236e19d024b42f8AE6780E44A573170703": "BTC/USD",
}

TOKENS = {
    "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1": "WETH",
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831": "USDC",
    "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f": "WBTC",
}


def get_account_position_list_key(account: str) -> bytes:
    """
    Calculate the account position list key using GMX's double-hash method:
    1. ACCOUNT_POSITION_LIST = keccak256(abi.encode("ACCOUNT_POSITION_LIST"))
    2. accountPositionListKey = keccak256(abi.encode(ACCOUNT_POSITION_LIST, account))
    """
    # First hash: keccak256(abi.encode("ACCOUNT_POSITION_LIST"))
    account_position_list_hash = Web3.keccak(encode(["string"], ["ACCOUNT_POSITION_LIST"]))

    # Second hash: keccak256(abi.encode(ACCOUNT_POSITION_LIST, account))
    account_position_list_key = Web3.keccak(encode(["bytes32", "address"], [account_position_list_hash, account]))

    return account_position_list_key


def get_position_key(account: str, market: str, collateral_token: str, is_long: bool) -> bytes:
    """
    Calculate position key: keccak256(abi.encodePacked(account, market, collateralToken, isLong))
    """
    # encodePacked concatenates without padding
    packed = bytes.fromhex(account[2:].lower()) + bytes.fromhex(market[2:].lower()) + bytes.fromhex(collateral_token[2:].lower()) + (b"\x01" if is_long else b"\x00")
    return Web3.keccak(packed)


def decode_position(position_data: bytes) -> dict:
    """
    Decode position data returned from Reader.getPosition()

    Position struct:
    - address account
    - address market
    - address collateralToken
    - uint256 sizeInUsd (30 decimals)
    - uint256 sizeInTokens (token decimals)
    - uint256 collateralAmount (token decimals)
    - int256 borrowingFactor
    - uint256 fundingFeeAmountPerSize
    - uint256 longTokenClaimableFundingAmountPerSize
    - uint256 shortTokenClaimableFundingAmountPerSize
    - uint256 increasedAtBlock
    - uint256 decreasedAtBlock
    - bool isLong
    - bool isCollateralLong
    """
    # Each field is 32 bytes
    account = Web3.to_checksum_address("0x" + position_data[12:32].hex())
    market = Web3.to_checksum_address("0x" + position_data[44:64].hex())
    collateral_token = Web3.to_checksum_address("0x" + position_data[76:96].hex())

    size_in_usd = int.from_bytes(position_data[96:128], "big")
    size_in_tokens = int.from_bytes(position_data[128:160], "big")
    collateral_amount = int.from_bytes(position_data[160:192], "big")

    increased_at_block = int.from_bytes(position_data[320:352], "big")
    decreased_at_block = int.from_bytes(position_data[352:384], "big")

    is_long = int.from_bytes(position_data[384:416], "big") == 1

    return {
        "account": account,
        "market": market,
        "collateral_token": collateral_token,
        "size_in_usd": size_in_usd / 1e30,  # GMX uses 30 decimals for USD
        "size_in_tokens": size_in_tokens / 1e18,  # Assuming 18 decimals (WETH/WBTC)
        "collateral_amount": collateral_amount / 1e18,  # Assuming 18 decimals
        "increased_at_block": increased_at_block,
        "decreased_at_block": decreased_at_block,
        "is_long": is_long,
    }


def fetch_positions(rpc_url: str, account: str):
    """Fetch and display all open positions for an account."""
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    if not w3.is_connected():
        print(f"❌ Failed to connect to RPC: {rpc_url}")
        sys.exit(1)

    print(f"✓ Connected to Arbitrum (Chain ID: {w3.eth.chain_id})")
    print(f"✓ Block number: {w3.eth.block_number}")
    print()

    # Get account position list key
    position_list_key = get_account_position_list_key(account)

    # Get position count
    position_count = w3.eth.call({"to": DATA_STORE, "data": Web3.keccak(text="getBytes32Count(bytes32)")[:4] + position_list_key})
    position_count = int.from_bytes(position_count, "big")

    print(f"Account: {account}")
    print(f"Open Positions: {position_count}")
    print()

    if position_count == 0:
        print("No open positions.")
        return

    # Get all position keys
    # Call: getBytes32ValuesAt(bytes32,uint256,uint256)
    function_selector = Web3.keccak(text="getBytes32ValuesAt(bytes32,uint256,uint256)")[:4]
    call_data = function_selector + encode(["bytes32", "uint256", "uint256"], [position_list_key, 0, position_count])

    result = w3.eth.call({"to": DATA_STORE, "data": call_data})

    # Decode bytes32[] return value
    # First 32 bytes: offset to array
    # Next 32 bytes: array length
    # Then: array elements (each 32 bytes)
    offset = int.from_bytes(result[0:32], "big")
    array_length = int.from_bytes(result[32:64], "big")

    position_keys = []
    for i in range(array_length):
        start = 64 + (i * 32)
        position_key = result[start : start + 32]
        position_keys.append(position_key)

    # Fetch details for each position
    print("=" * 80)
    for idx, position_key in enumerate(position_keys, 1):
        # Call Reader.getPosition(address dataStore, bytes32 key)
        function_selector = Web3.keccak(text="getPosition(address,bytes32)")[:4]
        call_data = function_selector + encode(["address", "bytes32"], [DATA_STORE, position_key])

        position_data = w3.eth.call({"to": READER, "data": call_data})
        position = decode_position(position_data)

        # Display position
        market_name = MARKETS.get(position["market"], position["market"])
        token_name = TOKENS.get(position["collateral_token"], position["collateral_token"])
        direction = "LONG 🟢" if position["is_long"] else "SHORT 🔴"

        print(f"Position #{idx}")
        print(f"  Market:           {market_name}")
        print(f"  Direction:        {direction}")
        print(f"  Collateral Token: {token_name}")
        print(f"  Position Size:    ${position['size_in_usd']:,.2f}")
        print(f"  Size in Tokens:   {position['size_in_tokens']:.6f} {token_name}")
        print(f"  Collateral:       {position['collateral_amount']:.6f} {token_name}")

        # Calculate leverage (approximate, assuming ETH = $3344)
        if position["collateral_amount"] > 0:
            eth_price = 3344  # You can fetch real price from oracle
            collateral_usd = position["collateral_amount"] * eth_price
            leverage = position["size_in_usd"] / collateral_usd
            print(f"  Collateral Value: ${collateral_usd:,.2f}")
            print(f"  Leverage:         {leverage:.2f}x")

        print(f"  Position Key:     0x{position_key.hex()}")
        print("=" * 80)


if __name__ == "__main__":
    rpc_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RPC_URL
    account = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ACCOUNT

    try:
        fetch_positions(rpc_url, account)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

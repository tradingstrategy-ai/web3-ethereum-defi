"""Raw log event data conversion helpers."""
from eth_typing import ChecksumAddress
from web3 import Web3


def decode_data(data: str) -> list[bytes]:
    """Split data of a log to uin256 results"""

    # {'address': '0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f', 'blockHash': '0x359d1dc4f14f9a07cba3ae8416958978ce98f78ad7b8d505925dad9722081f04', 'blockNumber': '0x98b723', 'data': '0x000000000000000000000000b4e16d0168e52d35cacd2c6185b44281ec28c9dc0000000000000000000000000000000000000000000000000000000000000001', 'logIndex': '0x22', 'removed': False, 'topics': ['0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9', '0x000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', '0x000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'], 'transactionHash': '0xd07cbde817318492092cc7a27b3064a69bd893c01cb593d6029683ffd290ab3a', 'transactionIndex': '0x26', 'event': <class 'web3._utils.datatypes.PairCreated'>, 'timestamp': 1588710145}
    b = bytes.fromhex(data[2:])
    entries = []
    for i in range(0, len(b), 32):
        entries.append(b[i : i + 32])
    return entries


def convert_uint256_bytes_to_address(raw: bytes) -> ChecksumAddress:
    """Convert raw uin256 from log data to addresses.

    .. note ::

        Ethereum address checksum might have a speed penalty for
        high speed operations.
    """
    assert len(raw) == 32
    return Web3.toChecksumAddress(raw[12:])


def convert_int256_bytes_to_int(bytes32: bytes, *, signed: bool = False) -> int:
    """Convert raw bytes32 from log data to addresses ints.

    :param signed:
        Default to unsigned uint256. Set true for int256.
    """
    return int.from_bytes(bytes32, "big", signed=signed)


def convert_uint256_string_to_address(bytes32: str) -> ChecksumAddress:
    """Convert raw uint256 from log data to address.

    .. note ::

        Ethereum address checksum might have a speed penalty for
        high speed operations.

    :param bytes32:
        E.g. `0x00000000000000000000000006af07097c9eeb7fd685c692751d5c66db49c215`
    """
    assert bytes32.startswith("0x")
    raw = bytes.fromhex(bytes32[2:])
    assert len(raw) == 32
    return Web3.toChecksumAddress(raw[12:])


def convert_uint256_string_to_int(bytes32: str, *, signed: bool = False) -> int:
    """Convert raw uint256 from log data to int.

    :param bytes32:
        E.g. `0x00000000000000000000000006af07097c9eeb7fd685c692751d5c66db49c215`
    """
    assert bytes32.startswith("0x")
    raw = bytes.fromhex(bytes32[2:])
    return int.from_bytes(raw, "big", signed=signed)


def convert_jsonrpc_value_to_int(val: str | int) -> int:
    """Convert hex string or int to int.

    Depending on the used JSON-RPC node,
    they may return hex encoded values or JSON numbers
    in JSON-RPC type. We need to be able to support both node and
    do the compatibility hack here.
    """

    if type(val) == int:
        # EthereumTester
        return val

    # Hex number
    return int(val, 16)

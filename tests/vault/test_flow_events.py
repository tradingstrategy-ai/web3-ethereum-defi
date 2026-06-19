"""Unit tests for vault flow event decoding helpers."""

import eth_abi

from eth_defi.vault.flow_events import (
    decode_hypersync_int,
    decode_indexed_event_address,
    decode_indexed_event_uint,
    decode_single_uint256_event_data,
    event_data_to_bytes,
    normalise_event_topic,
)

EXPECTED_TOPIC_UINT = 42
EXPECTED_RAW_ASSETS = 300_000_000
EXPECTED_NATIVE_INT = 26
EXPECTED_BARE_HEX_INT = 16


def test_event_topic_and_data_decoders() -> None:
    """Decode EVM log topics and event data without network access.

    1. Normalise topic and data encodings from bytes and hex strings.
    2. Decode indexed address and integer topics.
    3. Decode a single uint256 ABI data payload.
    """
    # 1. Normalise topic and data encodings from bytes and hex strings.
    assert normalise_event_topic(bytes.fromhex("ab" * 32)) == "0x" + "ab" * 32
    assert normalise_event_topic("cd" * 32) == "0x" + "cd" * 32
    assert event_data_to_bytes("0x1234") == bytes.fromhex("1234")
    assert event_data_to_bytes(bytes.fromhex("5678")) == bytes.fromhex("5678")

    # 2. Decode indexed address and integer topics.
    topic_address = "0x" + "00" * 12 + "81ae3f0d805d1ebab21d3b16175ee3dfa5a18656"
    assert decode_indexed_event_address(topic_address) == "0x81AE3f0D805D1EBAb21D3B16175eE3Dfa5a18656"
    assert decode_indexed_event_uint("0x" + "00" * 31 + "2a") == EXPECTED_TOPIC_UINT

    # 3. Decode a single uint256 ABI data payload.
    assert decode_single_uint256_event_data(eth_abi.encode(["uint256"], [EXPECTED_RAW_ASSETS])) == EXPECTED_RAW_ASSETS


def test_decode_hypersync_int_uses_hex_strings() -> None:
    """Decode Hypersync integer fields using the hex-string convention.

    1. Preserve native integer values.
    2. Decode prefixed hex strings.
    3. Decode bare hex strings as base 16, not base 10.
    """
    # 1. Preserve native integer values.
    assert decode_hypersync_int(EXPECTED_NATIVE_INT) == EXPECTED_NATIVE_INT

    # 2. Decode prefixed hex strings.
    assert decode_hypersync_int("0x1a") == EXPECTED_NATIVE_INT

    # 3. Decode bare hex strings as base 16, not base 10.
    assert decode_hypersync_int("10") == EXPECTED_BARE_HEX_INT

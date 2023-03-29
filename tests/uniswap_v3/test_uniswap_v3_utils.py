"""Test Uniswap V3 util functions."""
import pytest

from eth_defi.uniswap_v3.utils import encode_path, decode_path


@pytest.mark.parametrize(
    "path,fees,is_exact_output,expected_encoded_path",
    [
        (
            [
                "0x0000000000000000000000000000000000000001",
                "0x0000000000000000000000000000000000000002",
            ],
            [
                3000,
            ],
            False,
            "0x0000000000000000000000000000000000000001000bb80000000000000000000000000000000000000002",
        ),
        (
            [
                "0x0000000000000000000000000000000000000001",
                "0x0000000000000000000000000000000000000002",
            ],
            [
                3000,
            ],
            True,
            "0x0000000000000000000000000000000000000002000bb80000000000000000000000000000000000000001",
        ),
        (
            [
                "0x0000000000000000000000000000000000000001",
                "0x0000000000000000000000000000000000000002",
                "0x0000000000000000000000000000000000000003",
            ],
            [
                3000,
                500,
            ],
            False,
            "0x0000000000000000000000000000000000000001000bb800000000000000000000000000000000000000020001f40000000000000000000000000000000000000003",
        ),
        (
            [
                "0x0000000000000000000000000000000000000001",
                "0x0000000000000000000000000000000000000002",
                "0x0000000000000000000000000000000000000003",
            ],
            [
                3000,
                500,
            ],
            True,
            "0x00000000000000000000000000000000000000030001f40000000000000000000000000000000000000002000bb80000000000000000000000000000000000000001",
        ),
    ],
)
def test_encode_path(path, fees, is_exact_output, expected_encoded_path):
    """Test encode path.

    Based on: https://github.com/Uniswap/v3-sdk/blob/1a74d5f0a31040fec4aeb1f83bba01d7c03f4870/src/utils/encodeRouteToPath.test.ts
    """
    encoded = encode_path(path, fees, is_exact_output)
    assert type(encoded) == bytes
    hex_encoded = f"0x{encoded.hex()}"
    assert hex_encoded == expected_encoded_path


@pytest.mark.parametrize(
    "encoded_path, decoded_path",
    [
        (b"\xf2\xe2F\xbbv\xdf\x87l\xef\x8b8\xae\x84\x13\x0fOU\xde9[\x00\x0b\xb8\xb9\x81o\xc5yw\xd5\xa7\x86\xe6T\xc7\xcfvv{\xe6;\x96n", ["0xF2E246BB76DF876Cef8b38ae84130F4F55De395b", 3000, "0xB9816fC57977D5A786E654c7CF76767be63b966e"]),
        (
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x0b\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x01\xf4\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x03",
            [
                "0x0000000000000000000000000000000000000001",
                3000,
                "0x0000000000000000000000000000000000000002",
                500,
                "0x0000000000000000000000000000000000000003",
            ],
        ),
    ],
)
def test_decode_path(encoded_path: bytes, decoded_path: list):
    assert type(encoded_path == bytes)
    _decoded_path = decode_path(encoded_path)

    for i in range(len(decoded_path)):
        assert _decoded_path[i] == decoded_path[i]

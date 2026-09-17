"""Tests for native Lighter API-key generation."""

import pytest

from eth_defi.lighter import api_key
from eth_defi.lighter.api_key import ECGFP5_SCALAR_ORDER, generate_lighter_api_key
from eth_defi.lighter.pubkey import MAX_API_KEY_INDEX, MIN_API_KEY_INDEX, PUB_KEY_BYTES_SIZE

#: Reference pairs emitted by the official Lighter signer built from
#: poseidon_crypto commit ca0ad1bc8dfe822c61ba7556d1d4d5bcc2ea4a26.
REFERENCE_VECTORS = (
    (
        "ed844875833f87e80ad26837653759ce9f5288a17758e7cd704aa16a1578b6b77c5e0670ba8ed96a",
        "79eae45eeb0689a830214c2acc42f622e520d8b7eea30ecdaf826f0283cdc59d23300fb8f59515d9",
    ),
    (
        "b4da53b9fd3759e03f92fd7f5eb7c1609a0e7b287e2622091f651f4e1dbb1c800236a2e443890f27",
        "d301dc1580771e7a58b806b9ad791bc04764454cadf3bd442f9511b4706556a1e58666aa8de2cb93",
    ),
)


@pytest.mark.parametrize(("private_key", "expected_public_key"), REFERENCE_VECTORS)
def test_generate_lighter_api_key_matches_reference(monkeypatch: pytest.MonkeyPatch, private_key: str, expected_public_key: str) -> None:
    """Generated ECgFp5 public keys match official signer vectors."""
    private_scalar = int.from_bytes(bytes.fromhex(private_key), "little")
    monkeypatch.setattr(api_key.secrets, "randbelow", lambda _: private_scalar - 1)

    result = generate_lighter_api_key()

    assert result.private_key == "0x" + private_key
    assert result.public_key == "0x" + expected_public_key


def test_generate_lighter_api_key_uses_little_endian_non_zero_scalar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generated private keys add one to the ``randbelow`` result."""
    monkeypatch.setattr(api_key.secrets, "randbelow", lambda _: 0)

    result = generate_lighter_api_key()

    assert result.private_key == "0x" + "01" + "00" * 39
    assert len(bytes.fromhex(result.public_key.removeprefix("0x"))) == PUB_KEY_BYTES_SIZE
    assert int.from_bytes(bytes.fromhex(result.private_key.removeprefix("0x")), "little") == 1


def test_generate_lighter_api_key_accepts_boundary_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    """All user API-key slots can be generated without exposing the secret."""
    monkeypatch.setattr(api_key.secrets, "randbelow", lambda _: ECGFP5_SCALAR_ORDER - 2)

    low = generate_lighter_api_key(MIN_API_KEY_INDEX)
    high = generate_lighter_api_key(MAX_API_KEY_INDEX)

    assert low.api_key_index == MIN_API_KEY_INDEX
    assert high.api_key_index == MAX_API_KEY_INDEX
    assert low.private_key not in repr(low)


@pytest.mark.parametrize("api_key_index", (MIN_API_KEY_INDEX - 1, MAX_API_KEY_INDEX + 1))
def test_generate_lighter_api_key_rejects_reserved_or_invalid_index(api_key_index: int) -> None:
    """Reserved and out-of-range Lighter API-key slots fail before randomness."""
    with pytest.raises(ValueError, match="api_key_index"):
        generate_lighter_api_key(api_key_index)

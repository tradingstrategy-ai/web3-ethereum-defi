"""Native Lighter API-key generation.

This module replaces the ``lighter-sdk`` dependency for the one operation the
Lagoon deployer needs: generating a secret scalar and deriving its public key.
It deliberately does *not* implement Lighter transaction signing, API
authentication, nonce management or order submission.

The arithmetic is a small, non-constant-time Python port of the ECgFp5 public
key derivation in `poseidon_crypto`_ at commit
``ca0ad1bc8dfe822c61ba7556d1d4d5bcc2ea4a26`` (Apache-2.0), as used by
`lighter-go`_. It is suitable for one-off local key creation during deployment,
not for a general-purpose cryptography API.

.. _poseidon_crypto: https://github.com/elliottech/poseidon_crypto
.. _lighter-go: https://github.com/elliottech/lighter-go
"""

import secrets
from dataclasses import dataclass, field

from eth_defi.lighter.pubkey import GOLDILOCKS_MODULUS, MAX_API_KEY_INDEX, MIN_API_KEY_INDEX, validate_lighter_pubkey

#: Degree of the Goldilocks extension field used by ECgFp5.
EXTENSION_DEGREE = 5

#: Prime order of the ECgFp5 group from ``curve/ecgfp5/scalar_field.go``.
ECGFP5_SCALAR_ORDER = 1067993516717146951041484916571792702745057740581727230159139685185762082554198619328292418486241

_Fp5 = tuple[int, int, int, int, int]
_Point = tuple[_Fp5, _Fp5, _Fp5, _Fp5]
_ZERO: _Fp5 = (0, 0, 0, 0, 0)
_ONE: _Fp5 = (1, 0, 0, 0, 0)
_B: _Fp5 = (0, 263, 0, 0, 0)
_B_MUL2: _Fp5 = (0, 526, 0, 0, 0)
_B_MUL4: _Fp5 = (0, 1052, 0, 0, 0)
_FOUR: _Fp5 = (4, 0, 0, 0, 0)

#: ``(x, z, u, t)`` projective coordinates of the official generator.
_GENERATOR: _Point = (
    (12883135586176881569, 4356519642755055268, 5248930565894896907, 2165973894480315022, 2448410071095648785),
    _ONE,
    _ONE,
    _FOUR,
)
_NEUTRAL: _Point = (_ZERO, _ONE, _ZERO, _ONE)


@dataclass(slots=True, frozen=True)
class LighterApiKey:
    """A locally generated Lighter API-key pair.

    The private scalar is intentionally excluded from ``repr()``. Store it only
    in an explicitly secret-bearing deployment report.
    """

    #: Lighter API-key slot, 4 through 254.
    api_key_index: int

    #: 40-byte little-endian private scalar, hex encoded with a ``0x`` prefix.
    private_key: str = field(repr=False)

    #: 40-byte little-endian ECgFp5 public key, hex encoded with a ``0x`` prefix.
    public_key: str


def _fp5_add(left: _Fp5, right: _Fp5) -> _Fp5:
    """Add two Goldilocks quintic-extension elements.

    :param left: Left field element.
    :param right: Right field element.
    :return: Sum reduced by the Goldilocks modulus.
    """
    return tuple((left[i] + right[i]) % GOLDILOCKS_MODULUS for i in range(5))  # type: ignore[return-value]


def _fp5_sub(left: _Fp5, right: _Fp5) -> _Fp5:
    """Subtract two Goldilocks quintic-extension elements.

    :param left: Left field element.
    :param right: Right field element.
    :return: Difference reduced by the Goldilocks modulus.
    """
    return tuple((left[i] - right[i]) % GOLDILOCKS_MODULUS for i in range(5))  # type: ignore[return-value]


def _fp5_mul(left: _Fp5, right: _Fp5) -> _Fp5:
    """Multiply elements in ``F_p[X] / (X**5 - 3)``.

    :param left: Left field element.
    :param right: Right field element.
    :return: Product reduced in the quintic extension field.
    """
    result = [0] * EXTENSION_DEGREE
    for left_index, left_value in enumerate(left):
        for right_index, right_value in enumerate(right):
            index = left_index + right_index
            product = left_value * right_value
            if index >= EXTENSION_DEGREE:
                result[index - EXTENSION_DEGREE] += 3 * product
            else:
                result[index] += product
    return tuple(value % GOLDILOCKS_MODULUS for value in result)  # type: ignore[return-value]


def _fp5_square(value: _Fp5) -> _Fp5:
    """Square a Goldilocks quintic-extension element.

    :param value: Field element to square.
    :return: Squared field element.
    """
    return _fp5_mul(value, value)


def _fp5_inverse(value: _Fp5) -> _Fp5:
    """Return the multiplicative inverse of a non-zero extension element.

    :param value: Non-zero field element.
    :return: Multiplicative inverse calculated by exponentiation.
    """
    if value == _ZERO:
        message = "Cannot invert zero ECgFp5 element"
        raise ValueError(message)

    exponent = GOLDILOCKS_MODULUS**EXTENSION_DEGREE - 2
    result = _ONE
    base = value
    while exponent:
        if exponent & 1:
            result = _fp5_mul(result, base)
        base = _fp5_square(base)
        exponent >>= 1
    return result


def _point_add(left: _Point, right: _Point) -> _Point:  # noqa: PLR0914
    """Add ECgFp5 points with the official complete projective formula.

    :param left: Left point in ``(x, z, u, t)`` projective coordinates.
    :param right: Right point in ``(x, z, u, t)`` projective coordinates.
    :return: Sum in projective coordinates.
    """
    x1, z1, u1, t1_source = left
    x2, z2, u2, t2_source = right
    t1 = _fp5_mul(x1, x2)
    t2 = _fp5_mul(z1, z2)
    t3 = _fp5_mul(u1, u2)
    t4 = _fp5_mul(t1_source, t2_source)
    t5 = _fp5_sub(_fp5_mul(_fp5_add(x1, z1), _fp5_add(x2, z2)), _fp5_add(t1, t2))
    t6 = _fp5_sub(_fp5_mul(_fp5_add(u1, t1_source), _fp5_add(u2, t2_source)), _fp5_add(t3, t4))
    t7 = _fp5_add(t1, _fp5_mul(t2, _B))
    t8 = _fp5_mul(t4, t7)
    t9 = _fp5_mul(t3, _fp5_add(_fp5_mul(t5, _B_MUL2), _fp5_add(t7, t7)))
    t10 = _fp5_mul(_fp5_add(t4, _fp5_add(t3, t3)), _fp5_add(t5, t7))
    return (
        _fp5_mul(_fp5_sub(t10, t8), _B),
        _fp5_sub(t8, t9),
        _fp5_mul(t6, _fp5_sub(_fp5_mul(t2, _B), t1)),
        _fp5_add(t8, t9),
    )


def _point_double(point: _Point) -> _Point:
    """Double an ECgFp5 point with the official projective formula.

    :param point: Point in ``(x, z, u, t)`` projective coordinates.
    :return: Doubled point in projective coordinates.
    """
    x, z, u, t = point
    t1 = _fp5_mul(z, t)
    t2 = _fp5_mul(t1, t)
    x1 = _fp5_square(t2)
    z1 = _fp5_mul(t1, u)
    t3 = _fp5_square(u)
    w1 = _fp5_sub(t2, _fp5_mul(t3, _fp5_add(_fp5_add(x, z), _fp5_add(x, z))))
    t4 = _fp5_square(z1)
    z_new = _fp5_square(w1)
    return (
        _fp5_mul(t4, _B_MUL4),
        z_new,
        _fp5_sub(_fp5_square(_fp5_add(w1, z1)), _fp5_add(t4, z_new)),
        _fp5_sub(_fp5_add(x1, x1), _fp5_add(_fp5_mul(t4, _FOUR), z_new)),
    )


def _derive_public_key(private_scalar: int) -> bytes:
    """Derive a canonical 40-byte Lighter public key from a private scalar.

    :param private_scalar: Secret scalar within the ECgFp5 group order.
    :return: Affine public key as five little-endian Goldilocks limbs.
    """
    if not 0 < private_scalar < ECGFP5_SCALAR_ORDER:
        message = "Lighter private scalar must be within the ECgFp5 group order"
        raise ValueError(message)

    result = _NEUTRAL
    point = _GENERATOR
    scalar = private_scalar
    while scalar:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_double(point)
        scalar >>= 1

    _, _, u, t = result
    encoded = _fp5_mul(t, _fp5_inverse(u))
    return b"".join(limb.to_bytes(8, "little") for limb in encoded)


def generate_lighter_api_key(api_key_index: int = MIN_API_KEY_INDEX) -> LighterApiKey:
    """Generate a Lighter API-key pair for deployment registration.

    Uses Python's cryptographic random source for a non-zero scalar and derives
    only its public key locally. The caller must register the public key on
    L1 before using the private key for Lighter trading.

    :param api_key_index:
        Lighter API-key slot in the user range 4 through 254.

    :return:
        A private/public API-key pair. Its private key is not included in
        ``repr()``.
    """
    if not MIN_API_KEY_INDEX <= api_key_index <= MAX_API_KEY_INDEX:
        raise ValueError(f"api_key_index must be {MIN_API_KEY_INDEX}..{MAX_API_KEY_INDEX}, got {api_key_index}")

    private_scalar = secrets.randbelow(ECGFP5_SCALAR_ORDER - 1) + 1
    private_key = "0x" + private_scalar.to_bytes(40, "little").hex()
    public_key_bytes = _derive_public_key(private_scalar)
    validate_lighter_pubkey(public_key_bytes)
    return LighterApiKey(
        api_key_index=api_key_index,
        private_key=private_key,
        public_key="0x" + public_key_bytes.hex(),
    )

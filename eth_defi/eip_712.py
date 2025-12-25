"""EIP-712 signing.

- Routines for EIP712 encoding and signing.

- `Based on Gnosis utilities <https://raw.githubusercontent.com/safe-global/safe-eth-py/master/gnosis/eth/eip712/__init__.py>`__.

- Used in :py:mod:`eth_defi.usdc` module for crafting EIP-3009 messages

Example:

.. code-block:: python

    data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            authorization_type.value: [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        # domainSeparator = makeDomainSeparator(
        #   "USD Coin",
        #   "2",
        #   1, // hardcoded to 1 because of ganache bug: https://github.com/trufflesuite/ganache/issues/1643
        #   getFiatToken().address
        # );
        "domain": {
            "name": token.name,
            "version": "2",  # TODO: Read from USDC contract?
            "chainId": chain_id,
            "verifyingContract": token.address,
        },
        "primaryType": authorization_type.value,
        "message": {"from": from_, "to": to, "value": value, "validAfter": valid_after, "validBefore": valid_before, "nonce": secrets.token_bytes(32)},  # 256-bit random nonce
    }

    message_hash = eip712_encode_hash(data)
    if WEB3_PY_V7:
        signed_message = local_account.unsafe_sign_hash(message_hash)
    else:
        signed_message = local_account.signHash(message_hash)

Past copyright:

.. code-block:: text

    Copyright (C) 2022 Judd Vinet <jvinet@zeroflux.org>
                       Uxío Fuentefría <uxio@safe.global>

    Permission is hereby granted, free of charge, to any person obtaining a copy of
    this software and associated documentation files (the "Software"), to deal in
    the Software without restriction, including without limitation the rights to
    use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
    of the Software, and to permit persons to whom the Software is furnished to do
    so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

import re
from typing import Any, Dict, List, Union

from eth_abi import encode as encode_abi
from eth_account import Account
from eth_typing import Hash32, HexStr
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.compat import WEB3_PY_V7


# from ..utils import fast_keccak


def fast_keccak(value: bytes) -> bytes:
    return Web3.keccak(value)


def encode_data(primary_type: str, data, types):
    """
    Encode structured data as per Ethereum's signTypeData_v4.

    https://docs.metamask.io/guide/signing-data.html#sign-typed-data-v4

    This code is ported from the Javascript "eth-sig-util" package.
    """
    encoded_types = ["bytes32"]
    encoded_values = [hash_type(primary_type, types)]

    def _encode_field(name, typ, value):
        if typ in types:
            if value is None:
                return [
                    "bytes32",
                    "0x0000000000000000000000000000000000000000000000000000000000000000",
                ]
            else:
                return ["bytes32", fast_keccak(encode_data(typ, value, types))]

        if value is None:
            raise Exception(f"Missing value for field {name} of type {type}")

        # Accept string bytes
        if "bytes" in typ and isinstance(value, str):
            value = HexBytes(value)

        # Accept string uint and int
        if "int" in typ and isinstance(value, str):
            value = int(value)

        if typ == "bytes":
            return ["bytes32", fast_keccak(value)]

        if typ == "string":
            # Convert string to bytes.
            try:
                value = value.encode("utf-8")
            except AttributeError as e:
                raise RuntimeError(f"Could not encode: {typ}: {value}") from e
            return ["bytes32", fast_keccak(value)]

        if typ.endswith("]"):
            # Array type
            if value:
                parsed_type = typ[: typ.rindex("[")]
                type_value_pairs = [_encode_field(name, parsed_type, v) for v in value]
                data_types, data_hashes = zip(*type_value_pairs)
            else:
                # Empty array
                data_types, data_hashes = [], []

            h = fast_keccak(encode_abi(data_types, data_hashes))
            return ["bytes32", h]

        return [typ, value]

    for field in types[primary_type]:
        typ, val = _encode_field(field["name"], field["type"], data[field["name"]])
        encoded_types.append(typ)
        encoded_values.append(val)

    return encode_abi(encoded_types, encoded_values)


def encode_type(primary_type: str, types) -> str:
    result = ""
    deps = find_type_dependencies(primary_type, types)
    deps = sorted([d for d in deps if d != primary_type])
    deps = [primary_type] + deps
    for typ in deps:
        children = types[typ]
        if not children:
            raise Exception(f"No type definition specified: {type}")

        defs = [f"{t['type']} {t['name']}" for t in types[typ]]
        result += typ + "(" + ",".join(defs) + ")"
    return result


def find_type_dependencies(primary_type: str, types, results=None):
    if results is None:
        results = []

    primary_type = re.split(r"\W", primary_type)[0]
    if primary_type in results or not types.get(primary_type):
        return results
    results.append(primary_type)

    for field in types[primary_type]:
        deps = find_type_dependencies(field["type"], types, results)
        for dep in deps:
            if dep not in results:
                results.append(dep)

    return results


def hash_type(primary_type: str, types) -> Hash32:
    return fast_keccak(encode_type(primary_type, types).encode())


def hash_struct(primary_type: str, data, types) -> Hash32:
    return fast_keccak(encode_data(primary_type, data, types))


def eip712_encode(typed_data: Dict[str, Any]) -> List[bytes]:
    """
    Given a dict of structured data and types, return a 3-element list of
    the encoded, signable data.

      0: The magic & version (0x1901)
      1: The encoded types
      2: The encoded data
    """
    try:
        parts = [
            bytes.fromhex("1901"),
            hash_struct("EIP712Domain", typed_data["domain"], typed_data["types"]),
        ]
        if typed_data["primaryType"] != "EIP712Domain":
            parts.append(
                hash_struct(
                    typed_data["primaryType"],
                    typed_data["message"],
                    typed_data["types"],
                )
            )
        return parts
    except (KeyError, AttributeError, TypeError, IndexError) as exc:
        raise ValueError(f"Not valid {typed_data}") from exc


def eip712_encode_hash(typed_data: Dict[str, Any]) -> Hash32:
    """
    :param typed_data: EIP712 structured data and types
    :return: Keccak256 hash of encoded signable data
    """
    return fast_keccak(b"".join(eip712_encode(typed_data)))


def eip712_signature(payload: Dict[str, Any], private_key: Union[HexStr, bytes]) -> bytes:
    """
    Given a bytes object and a private key, return a signature suitable for
    EIP712 and EIP191 messages.
    """
    if isinstance(payload, (list, tuple)):
        payload = b"".join(payload)

    if isinstance(private_key, str) and private_key.startswith("0x"):
        private_key = private_key[2:]
    elif isinstance(private_key, bytes):
        private_key = bytes.hex()

    account = Account.from_key(private_key)
    hashed_payload = fast_keccak(payload)

    if WEB3_PY_V7:
        signed_message = account.unsafe_sign_hash(hashed_payload)
    else:
        signed_message = account.signHash(hashed_payload)

    return signed_message["signature"]

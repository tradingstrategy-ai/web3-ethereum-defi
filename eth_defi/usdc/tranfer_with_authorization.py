"""EIP-3009 transferWithAuthorization() support for Python.

- `The spec <https://github.com/ethereum/EIPs/issues/3010>`__.

- `JavaScript example <https://github.com/ZooWallet/safe-contracts/blob/b2abebd0fdd7f8a846dfc2d59233e41487b659cf/scripts/usdc-biconomy-transferWithAuthorization.js#L87>`__.

- `Canonical examples to construct USDC EIP-712 messages <https://github.com/centrehq/centre-tokens/blob/master/test/v2/GasAbstraction/helpers.ts>`__.

- `EIP-712 helpers by Consensys <https://github.com/ConsenSysMesh/py-eip712-structs>`__

"""
import datetime
import secrets
from typing import Tuple

import eip712_structs
from eth_account import Account
from eth_account._utils.signing import to_bytes32
from eth_account.datastructures import SignedMessage
from eth_account.messages import encode_structured_data, encode_defunct
from eth_account.signers.local import LocalAccount
from eth_utils import to_bytes
from web3.contract.contract import ContractFunction

from eth_defi.abi import encode_function_call
from eth_defi.eip_712 import eip712_encode_hash
from eth_defi.token import TokenDetails
from eth_typing import HexAddress, ChecksumAddress


from eip712_structs import EIP712Struct, String, Uint, Address

class TransferWithAuthorization(EIP712Struct):
    # TODO
    pass


# from keyword conflict workaround https://github.com/ConsenSysMesh/py-eip712-structs#dynamic-construction
class ReceiveWithAuthorization(EIP712Struct):
    pass

setattr(ReceiveWithAuthorization, 'from', eip712_structs.Address())
ReceiveWithAuthorization.to = eip712_structs.Address()
ReceiveWithAuthorization.value = eip712_structs.Uint(256)
ReceiveWithAuthorization.validAfter = eip712_structs.Uint(256)
ReceiveWithAuthorization.validBefore = eip712_structs.Uint(256)
ReceiveWithAuthorization.nonce = eip712_structs.Bytes(32)

#


def construct_receive_with_authorization_message_old_2(
    chain_id: int,
    token: TokenDetails,
    from_,
    to,
    value,
    valid_before=0,
    valid_after=1,
    duration_seconds=0,
) -> Tuple[EIP712Struct, EIP712Struct]:
    """Create EIP-721 message for transferWithAuthorization.

    - Skip the awful approve() step

    - Used to construct the message that then needs to be signed

    - The signature will be verified by `receiveWithAuthorization()`

    :return:
        EIP-712 data.

        Tuple(message, domain)
    """

    assert duration_seconds or valid_before, "You need to give either duration_seconds or valid_before"

    # Relative to the current time
    if duration_seconds:
        assert not valid_before, "You cannot give valid_before with duration_seconds"
        assert duration_seconds > 0
        valid_before = int(datetime.datetime.utcnow().timestamp() + duration_seconds)

    receive_with_authorization = ReceiveWithAuthorization()
    receive_with_authorization["from"] = from_
    receive_with_authorization["to"] = to
    receive_with_authorization["value"] = value
    receive_with_authorization["validBefore"] = valid_before
    receive_with_authorization["validAfter"] = valid_after
    receive_with_authorization["nonce"] = secrets.token_bytes(32)

    # domainSeparator = makeDomainSeparator(
    #   "USD Coin",
    #   "2",
    #   1, // hardcoded to 1 because of ganache bug: https://github.com/trufflesuite/ganache/issues/1643
    #   getFiatToken().address
    # );

    domain = eip712_structs.make_domain(
        name=token.name,
        version="2",
        chainId=1,
        verifyingContract=token.contract.address,
    )

    return receive_with_authorization, domain


def construct_receive_with_authorization_message(
    chain_id: int,
    token: TokenDetails,
    from_,
    to,
    value,
    valid_before=0,
    valid_after=1,
    duration_seconds=0,
) -> dict:
    """Create EIP-721 message for transferWithAuthorization.

    - Skip the awful approve() step

    - Used to construct the message that then needs to be signed

    - The signature will be verified by `receiveWithAuthorization()`
    """

    assert duration_seconds or valid_before, "You need to give either duration_seconds or valid_before"

    # Relative to the current time
    if duration_seconds:
        assert not valid_before, "You cannot give valid_before with duration_seconds"
        assert duration_seconds > 0
        valid_before = int(datetime.datetime.utcnow().timestamp() + duration_seconds)

    data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "ReceiveWithAuthorization": [
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
            "version": "2",
            "chainId": chain_id,
            "verifyingContract": token.address,
        },
        "primaryType": "ReceiveWithAuthorization",
        "message": {
            "from": from_,
            "to": to,
            "value": value,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": secrets.token_bytes(32)  # 256-bit random nonce
        },
    }
    return data


def construct_transfer_with_authorization_message_old(
        verifying_contract_address: HexAddress,
        token_name: str,
        token_version: str,
        chain_id: int,
        recipient_address: HexAddress,
        amount: int,
        user_address: HexAddress,
        valid_before: int = 0,
        valid_after: int = 1,
        duration_seconds: float = 0,
) -> dict:
    """Create EIP-721 message for transferWithAuthorization.

    - Skip the awful approve() step

    - Used to construct the message that then needs to be signed

    - The signature will be verified by `receiveWithAuthorization()`
    """

    assert duration_seconds or valid_before, "You need to give either duration_seconds or valid_before"

    # Relative to the current time
    if duration_seconds:
        assert not valid_before, "You cannot give valid_before with duration_seconds"
        assert duration_seconds > 0
        valid_before = int(datetime.datetime.utcnow().timestamp() + duration_seconds)

    data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "domain": {
            "name": token_name,
            "version": token_version,
            "chainId": chain_id,
            "verifyingContract": verifying_contract_address,
        },
        "primaryType": "TransferWithAuthorization",
        "message": {
            "from": user_address,
            "to": recipient_address,
            "value": amount,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": secrets.token_bytes(32)  # 256-bit random nonce
        },
    }
    return data


def make_receive_with_authorization_transfer(
    token: TokenDetails,
    from_: LocalAccount,
    to: HexAddress,
    func: ContractFunction,
    value: int,
    token_version = "2",  # TODO: Not sure if used
    valid_before: int = 0,
    valid_after: int = 1,
    duration_seconds: int = 0,
) -> ContractFunction:
    """Perform an EIP-3009 transaction.

    - Constructs the EIP-3009 payload

    - Signs the message

    - Builds a transaction against `transferWithAuthorization`

    .. note ::

        This currently supports only `LocalAccount` because of
        `missing features in web3.py <https://github.com/ethereum/web3.py/issues/2180#issuecomment-943590192>`__.

    The `receiveAuthorization()` signature is:

    .. code-block:: text

        function receiveWithAuthorization(
            address from,
            address to,
            uint256 value,
            uint256 validAfter,
            uint256 validBefore,
            bytes32 nonce,
            uint8 v,
            bytes32 r,
            bytes32 s)

    :param func:
        The contract function that is verifying the transfer.

        A smart contract function with the same call signature as receiveAuthorization().
        However, the spec does not specify what kind of a signature of a function this is:
        you can transfer `receiveWithAuthorization()` payload in any form, with extra parameters,
        byte packed, etc.

    :return:
        Bound contract function for transferWithAuthorization

    """

    assert isinstance(token, TokenDetails)
    assert isinstance(func, ContractFunction)
    assert isinstance(from_, LocalAccount)
    assert to.startswith("0x")
    assert value > 0

    web3 = token.contract.w3
    chain_id = web3.eth.chain_id

    # message, domain = construct_receive_with_authorization_message(
    #     chain_id=chain_id,
    #     token=token,
    #     from_=from_.address,
    #     to=to,
    #     value=value,
    #     valid_before=valid_before,
    #     valid_after=valid_after,
    #     duration_seconds=duration_seconds,
    # )
    #
    # encoded_data = message.signable_bytes(domain)
    #
    # signable_message = encode_defunct(encoded_data)
    #
    # # https://web3py.readthedocs.io/en/stable/web3.eth.html#web3.eth.Eth.sign
    # signed_message: SignedMessage = from_.sign_message(signable_message)

    data = construct_receive_with_authorization_message(
        chain_id=chain_id,
        token=token,
        from_=from_.address,
        to=to,
        value=value,
        valid_before=valid_before,
        valid_after=valid_after,
        duration_seconds=duration_seconds,
    )

    # The message payload is receiveAuthorization arguments, tightly encoded,
    # without the function selector
    message_hash = eip712_encode_hash(data)
    signed_message = from_.signHash(message_hash)
    # Should come in the order defined for the dict,
    # as Python 3.10+ does ordered dicts
    args = list(data["message"].values())  # from, to, value, validAfter, validBefore, nonce
    args += [signed_message.v, to_bytes32(signed_message.r), to_bytes32(signed_message.s)]

    return func(*args)

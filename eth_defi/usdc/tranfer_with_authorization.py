"""EIP-3009 transferWithAuthorization() support for Python.

- `The spec <https://github.com/ethereum/EIPs/issues/3010>`__.

- `JavaScript example <https://github.com/ZooWallet/safe-contracts/blob/b2abebd0fdd7f8a846dfc2d59233e41487b659cf/scripts/usdc-biconomy-transferWithAuthorization.js#L87>`__.

"""
import datetime
import secrets

from eth_account import Account
from eth_account.messages import encode_structured_data
from eth_account.signers.local import LocalAccount
from web3.contract.contract import ContractFunction

from eth_defi.token import TokenDetails
from eth_typing import HexAddress, ChecksumAddress


def construct_transfer_with_authorization_message(
        verifying_contract_address: HexAddress,
        token_name: str,
        token_version: str,
        token_address: HexAddress,
        chain_id: int,
        recipient_address: HexAddress,
        amount: int,
        user_address: HexAddress,
        valid_before: int,
        valid_after: int = 0,
        duration_seconds: float = 0,
) -> dict:
    """Execute EIP-3009 single click ERc-20 transfers.

    Skip the awful approve() step.
    """

    assert duration_seconds or valid_after, "You need to give either duration_seconds or valid_after"

    # Relative to the current time
    if duration_seconds:
        assert not valid_after, "You cannot give valid_after with duration_seconds"
        assert duration_seconds > 0
        valid_after = int(datetime.datetime.utcnow().timestamp() + duration_seconds)

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
            "nonce": secrets.token_hex(32)  # 256-bit random nonce
        },
    }
    return data


def transfer_with_authorization(
    token: TokenDetails,
    from_: HexAddress,
    to: HexAddress,
    func: ContractFunction,
    amount: int,
    token_version="2",  # TODO: Not sure if used
    duration_seconds=3600,
) -> ContractFunction:
    """Perform an EIP-3009 transaction.

    - Constructs the EIP-3009 payload

    - Signs the message

    - Builds a transaction against `transferWithAuthorization`

    :return:
        Bound contract function for transferWithAuthorization

    """

    assert isinstance(token, TokenDetails)
    assert isinstance(func, ContractFunction)
    assert from_.startswith("0x")
    assert to.startswith("0x")
    assert duration_seconds > 0
    assert amount > 0

    web3 = token.contract.w3
    chain_id = web3.eth.chain_id
    verifying_contract_address = func.address

    data = construct_transfer_with_authorization_message(
        verifying_contract_address=verifying_contract_address,
        token_name=token.name,
        token_version=token_version,
        token_address=token.address,
        chain_id=chain_id,
        recipient_address=to,
        amount=amount,
        user_address=from_,
        valid_before=0,
        duration_seconds=duration_seconds,
    )

    # https://ethereum.stackexchange.com/a/114217/620
    encoded_data = encode_structured_data(data)

    # https://web3py.readthedocs.io/en/stable/web3.eth.html#web3.eth.Eth.sign
    signed_message = web3.eth.sign(from_, encoded_data)

    return func(
        data["from"],
        data["to"],
        data["value"],
        data["validAfter"],
        data["validBefore"],
        data["nonce"],
        v=signed_message.v,
        r=signed_message.r,
        s=signed_message.s,
    )

"""EIP-3009 transferWithAuthorization() support for Python.

`See example in the spec <https://github.com/ethereum/EIPs/issues/3010>`__.
"""
import datetime
import secrets

from eth_account import Account
from eth_defi.token import TokenDetails
from eth_typing import HexAddress


def construct_transfer_with_authorization_message(
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
            "verifyingContract": token_address,
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
    account: Account,
    token: TokenDetails,
    to: HexAddress,
    amount: int,
):
    """Build an EIP-3009 transaction."""
    web3 = token.contract.w3
    chain_id = web3.eth.chain_id

    data = construct_transfer_with_authorization_message(

    )

    # https://ethereum.stackexchange.com/a/114217/620
    account.sign_message()

"""Some EIP-712 integration testing based on Centre's code."""

import pytest
from eth_account import Account
from eth_account._utils.signing import to_bytes32
from eth_account.signers.local import LocalAccount
from web3.contract import Contract

from eth_defi.deploy import deploy_contract
from eth_defi.eip_712 import eip712_encode_hash
from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil
from eth_defi.token import TokenDetails

from eth_defi.usdc.eip_3009 import construct_eip_3009_authorization_message


@pytest.fixture
def user(web3, deployer, usdc) -> LocalAccount:
    """Create a LocalAccount user.

    See limitations in `transfer_with_authorization`.
    """
    account = Account.create()
    web3.eth.send_transaction({"from": deployer, "to": account.address, "value": 9 * 10**18})  # Feed 9 ETH
    usdc.contract.functions.transfer(
        account.address,
        500 * 10**6,
    ).transact({"from": deployer})
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware_anvil(account))
    return account


@pytest.fixture
def eip_712_test(web3, deployer, usdc) -> Contract:
    contract = deploy_contract(
        web3,
        "centre/EIP712Test.json",
        deployer,
    )
    return contract


@pytest.fixture
def ecrecover_test(web3, deployer) -> Contract:
    contract = deploy_contract(
        web3,
        "centre/ECRecoverTest.json",
        deployer,
    )
    return contract


def test_ec_recover(
    web3,
    usdc: TokenDetails,
    ecrecover_test,
    deployer,
    user: LocalAccount,
):
    """Check we sign message hash correctly.

    - Use Centre ECRecoverTest contract
    """

    block = web3.eth.get_block("latest")
    valid_before = block["timestamp"] + 3600
    valid_after = 1
    from_ = user
    chain_id = web3.eth.chain_id
    value = 10
    token = usdc
    duration_seconds = None
    to = ecrecover_test.address

    data = construct_eip_3009_authorization_message(
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
    signed_message = from_.unsafe_sign_hash(message_hash)
    # Should come in the order defined for the dict,
    # as Python 3.10+ does ordered dicts
    args = list(data["message"].values())  # from, to, value, validAfter, validBefore, nonce
    args += [signed_message.v, to_bytes32(signed_message.r), to_bytes32(signed_message.s)]

    recovered = ecrecover_test.functions.recover(
        message_hash,
        signed_message.v,
        to_bytes32(signed_message.r),
        to_bytes32(signed_message.s),
    ).call()
    assert recovered == user.address

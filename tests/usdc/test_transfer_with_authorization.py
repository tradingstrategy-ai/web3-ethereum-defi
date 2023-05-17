import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3.contract import Contract
from web3.middleware import construct_sign_and_send_raw_middleware

from eth_defi.deploy import deploy_contract
from eth_defi.token import TokenDetails
from eth_defi.usdc.tranfer_with_authorization import transfer_with_authorization


@pytest.fixture
def user(web3, deployer, usdc) -> LocalAccount:
    """Create a LocalAccount user.

    See limitations in `transfer_with_authorization`.
    """

    account = Account.create()
    web3.eth.send_transaction({"from": deployer, "to": account.address, "value": 9 * 10**18})  # Feed 9 ETH
    usdc.contract.functions.transfer(account.address, 500 * 10**6,).transact({"from": deployer})
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))
    return account


@pytest.fixture
def receiver(web3, deployer, usdc) -> Contract:
    """The contract that handles incoming transferWithAuthorization()"""
    contract = deploy_contract(
         web3,
         "MockEIP3009Receiver.json",
         deployer,
        # Constructor args
        usdc.address,
    )
    return contract


def test_receive_authorization(
        usdc: TokenDetails,
        receiver,
        deployer,
        user: LocalAccount,
):
    """Directly call receiveAuthorization() hook

    - This will uncover any reverts

    - Normal code must never do this
    """

    bound_func = transfer_with_authorization(
        token=usdc,
        from_=user,
        to=receiver.address,
        func=receiver.functions.deposit,
        amount=500 * 10**6,  # 500 USD,
    )

    bound_func.transact({
        "from": deployer,
    })

def test_transfer_with_authorization(
        usdc: TokenDetails,
        receiver,
        deployer,
        user: LocalAccount,
):
    """See the transferWithAuthorization goes through."""

    bound_func = transfer_with_authorization(
        token=usdc,
        from_=user,
        to=receiver.address,
        func=receiver.functions.deposit,
        amount=500 * 10**6,  # 500 USD,
    )

    bound_func.transact({
        "from": deployer,
    })



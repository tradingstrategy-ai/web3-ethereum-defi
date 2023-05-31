""" EIP-3009 tests

- Test against MockEIP3009Receiver

- ERC-20 and approve() must die in flames

- For more EIP-3009 tests, see Enzyme test suite

"""
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.deploy import deploy_contract
from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.usdc.eip_3009 import make_eip_3009_transfer, EIP3009AuthorizationType


@pytest.fixture
def user(web3, deployer, usdc) -> LocalAccount:
    """Create a LocalAccount user.

    See limitations in `transfer_with_authorization`.
    """
    account = Account.create()
    stash = web3.eth.get_balance(deployer)
    tx_hash = web3.eth.send_transaction({"from": deployer, "to": account.address, "value": stash // 2})
    assert_transaction_success_with_explanation(web3, tx_hash)
    usdc.contract.functions.transfer(
        account.address,
        500 * 10**6,
    ).transact({"from": deployer})
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware_anvil(account))
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


def test_receive_with_authorization(
    web3,
    usdc: TokenDetails,
    receiver,
    deployer,
    user: LocalAccount,
):
    """See the transferWithAuthorization goes through."""

    assert usdc.contract.functions.balanceOf(user.address).call() == 500 * 10**6

    block = web3.eth.get_block("latest")

    # The transfer will expire in one hour
    # in the test EVM timeline
    valid_before = block["timestamp"] + 3600

    # Construct bounded ContractFunction instance
    # that will transact with MockEIP3009Receiver.deposit()
    # smart contract function.
    bound_func: ContractFunction = make_eip_3009_transfer(
        token=usdc,
        from_=user,
        to=receiver.address,
        func=receiver.functions.deposit,
        value=500 * 10**6,  # 500 USD,
        valid_before=valid_before,
        authorization_type=EIP3009AuthorizationType.ReceiveWithAuthorization,
    )

    # Sign and broadcast the tx
    tx_hash = bound_func.transact(
        {
            "from": user.address,
            "gas": 5_000_000,
        }
    )

    # Print out Solidity stack trace if this fails
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert receiver.functions.amountReceived().call() == 500 * 10**6

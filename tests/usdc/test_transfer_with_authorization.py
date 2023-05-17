import eip712_structs
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.middleware import construct_sign_and_send_raw_middleware

from eth_defi.deploy import deploy_contract
from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.usdc.tranfer_with_authorization import make_receive_with_authorization_transfer, ReceiveWithAuthorization


@pytest.fixture
def user(web3, deployer, usdc) -> LocalAccount:
    """Create a LocalAccount user.

    See limitations in `transfer_with_authorization`.
    """
    account = Account.create()
    web3.eth.send_transaction({"from": deployer, "to": account.address, "value": 9 * 10**18})  # Feed 9 ETH
    usdc.contract.functions.transfer(account.address, 500 * 10**6,).transact({"from": deployer})
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


def test_receive_with_authorization_type_hash():
    """Check we generate good type hash.

    Spent too much time having validBefore and validAfter in the wrong order.
    """
    # domainSeparator = makeDomainSeparator(
    #   "USD Coin",
    #   "2",
    #   1, // hardcoded to 1 because of ganache bug: https://github.com/trufflesuite/ganache/issues/1643
    #   getFiatToken().address
    # );

    #     // keccak256("ReceiveWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)")
    #     bytes32
    #         public constant RECEIVE_WITH_AUTHORIZATION_TYPEHASH = 0xd099cc98ef71107a616c4f0f941f04c322d8e254fe26b3c6668db87aae413de8;

    type_hash = ReceiveWithAuthorization.type_hash()
    assert type_hash.hex() == "d099cc98ef71107a616c4f0f941f04c322d8e254fe26b3c6668db87aae413de8"


def test_receive_with_authorization(
        web3,
        usdc: TokenDetails,
        receiver,
        deployer,
        user: LocalAccount,
):
    """See the transferWithAuthorization goes through."""

    block = web3.eth.get_block("latest")

    # The transfer will expire in one hour
    # in the test EVM timeline
    valid_before = block["timestamp"] + 3600

    # Construct bounded ContractFunction instance
    # that will transact with MockEIP3009Receiver.deposit()
    # smart contract function.
    bound_func: ContractFunction = make_receive_with_authorization_transfer(
        token=usdc,
        from_=user,
        to=receiver.address,
        func=receiver.functions.deposit,
        value=500 * 10**6,  # 500 USD,
        valid_before=valid_before,
    )

    # Sign and broadcast the tx
    tx_hash = bound_func.transact({
        "from": user.address,
        "gas": 15_000_000,
    })

    # Print out Solidity stack trace if this fails
    assert_transaction_success_with_explanation(web3, tx_hash)

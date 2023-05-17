import pytest
from web3.contract import Contract

from eth_defi.deploy import deploy_contract
from eth_defi.token import TokenDetails
from eth_defi.usdc.tranfer_with_authorization import transfer_with_authorization


@pytest.fixture
def receiver(web3, deployer) -> Contract:
    """The contract that handles """
    contract = deploy_contract(
         web3,
         "MockEIP3009Receiver.json",
         deployer,
    )
    return contract


def test_transfer_with_authorization(
        usdc: TokenDetails,
        receiver,
        deployer):
    """See the transferWithAuthorization goes through."""

    bound_func = transfer_with_authorization(
        token=usdc,
        from_=deployer,
        func=receiver.functions.deposit,
        amount=500 * 10**6,  # 500 USD,
    )

    bound_func.transact({
        "from": deployer,
    })



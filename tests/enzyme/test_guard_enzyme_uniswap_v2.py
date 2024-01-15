"""Enzyme integration tests for guard,

- Check Uniswap v2 access rights

- Check some negative cases for unauthroised transactions
"""
import datetime
import random

from eth_defi.abi import get_deployed_contract
from terms_of_service.acceptance_message import get_signing_hash, generate_acceptance_message, sign_terms_of_service

"""Enzyme USDC EIP-3009 payment forwarder.

- transferWithAuthorization() and receiveWithAuthorization() integration tests for Enzyme protocol
"""
import flaky
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, ChecksumAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.vault import Vault
from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.usdc.deployment import deploy_fiat_token
from eth_defi.usdc.eip_3009 import make_eip_3009_transfer, EIP3009AuthorizationType


@pytest.fixture()
def usdc(web3, deployer: ChecksumAddress) -> TokenDetails:
    """Centre fiat token.

    Deploy real USDC code.
    """
    return deploy_fiat_token(web3, deployer)


@pytest.fixture
def vault_owner(web3, deployer, usdc) -> Account:
    return web3.eth.accounts[1]


@pytest.fixture
def asset_manager(web3, deployer, usdc) -> Account:
    """Create a LocalAccount user.

    See limitations in `transfer_with_authorization`.
    """
    return web3.eth.accounts[2]


@pytest.fixture
def vault_investor(web3, deployer, usdc) -> LocalAccount:
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


@pytest.fixture()
def acceptance_message(web3: Web3) -> str:
    """The message user needs to sign in order to deposit."""

    # Generate the message user needs to sign in their wallet
    signing_content = generate_acceptance_message(
        1,
        datetime.datetime.utcnow(),
        "https://example.com/terms-of-service",
        random.randbytes(32),
    )

    return signing_content


@pytest.fixture()
def terms_of_service(
    web3: Web3,
    deployer: str,
    acceptance_message: str,
) -> Contract:
    """Deploy Terms of Service contract."""

    tos = deploy_contract(
        web3,
        "terms-of-service/TermsOfService.json",
        deployer,
    )

    new_version = 1
    new_hash = get_signing_hash(acceptance_message)
    tx_hash = tos.functions.updateTermsOfService(new_version, new_hash).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return tos


@pytest.fixture()
def vault(
    web3: Web3,
    deployer: HexAddress,
    weth: Contract,
    mln: Contract,
    usdc: TokenDetails,
    usdc_usd_mock_chainlink_aggregator: Contract,
    terms_of_service: Contract,
    acceptance_message: str,
) -> Vault:
    """Deploy an Enzyme vault.

    - Guard

    - Payment forwarder with terms of service signing
    """

    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        mln,
        weth,
    )

    deployment.add_primitive(
        usdc.contract,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    comptroller, vault = deployment.create_new_vault(
        deployer,
        usdc.contract,
    )

    assert comptroller.functions.getDenominationAsset().call() == usdc.address
    assert vault.functions.getTrackedAssets().call() == [usdc.address]

    payment_forwarder = deploy_contract(
        web3,
        "TermedVaultUSDCPaymentForwarder.json",
        deployer,
        usdc.address,
        comptroller.address,
        terms_of_service.address,
    )

    vault = Vault.fetch(web3, vault_address=vault.address, payment_forwarder=payment_forwarder.address)
    return vault


@pytest.fixture()
def payment_forwarder(vault: Vault) -> Contract:
    return vault.payment_forwarder


def test_enzyme_usdc_payment_forwarder_transfer_with_authorization_and_terms(
    web3: Web3,
    deployer: HexAddress,
    vault: Vault,
    vault_investor: LocalAccount,
    weth: Contract,
    mln: Contract,
    usdc: TokenDetails,
    usdc_usd_mock_chainlink_aggregator: Contract,
    payment_forwarder: Contract,
    acceptance_message: str,
    terms_of_service: Contract,
):
    """Buy shares using USDC payment forwader."""

    assert payment_forwarder.functions.isTermsOfServiceEnabled().call()

    # Pre-check the terms of service offers us the terms to be
    # signed as we expect
    terms_of_service_2 = get_deployed_contract(
        web3,
        "terms-of-service/TermsOfService.json",
        payment_forwarder.functions.termsOfService().call(),
    )
    assert terms_of_service_2.functions.latestTermsOfServiceVersion().call() == 1
    message_hash = get_signing_hash(acceptance_message)
    assert terms_of_service_2.functions.latestAcceptanceMessageHash().call() == message_hash

    # Sign terms of service
    acceptance_hash, signature = sign_terms_of_service(vault_investor, acceptance_message)
    assert len(acceptance_hash) == 32
    assert len(signature) == 65

    # The transfer will expire in one hour
    # in the test EVM timeline
    block = web3.eth.get_block("latest")
    valid_before = block["timestamp"] + 3600

    # Construct bounded ContractFunction instance
    # that will transact with MockEIP3009Receiver.deposit()
    # smart contract function.
    bound_func = make_eip_3009_transfer(
        token=usdc,
        from_=vault_investor,
        to=payment_forwarder.address,
        func=payment_forwarder.functions.buySharesOnBehalfUsingTransferWithAuthorizationAndTermsOfService,
        value=500 * 10**6,  # 500 USD,
        valid_before=valid_before,
        # uint256 minSharesQuantity,
        # bytes32 termsOfServiceHash,
        # bytes32 termsOfServiceSignature
        extra_args=(1, acceptance_hash, signature),
        authorization_type=EIP3009AuthorizationType.TransferWithAuthorization,
    )

    # Sign and broadcast the tx
    tx_hash = bound_func.transact(
        {
            "from": vault_investor.address,
        }
    )

    # Print out Solidity stack trace if this fails
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert payment_forwarder.functions.amountProxied().call() == 500 * 10**6  # Got shares

    vault = Vault.fetch(web3, vault_address=vault.address, payment_forwarder=payment_forwarder.address)

    assert vault.get_gross_asset_value() == 500 * 10**6  # Vault has been funded
    assert vault.vault.functions.balanceOf(vault_investor.address).call() == 500 * 10**18  # Got shares
    assert vault.payment_forwarder.address == payment_forwarder.address
    assert vault.payment_forwarder.functions.amountProxied().call() == 500 * 10**6

    # Terms of service successfully signed
    # (would fail earlier, but we check here just for an example)
    assert terms_of_service.functions.canAddressProceed(vault_investor.address).call()

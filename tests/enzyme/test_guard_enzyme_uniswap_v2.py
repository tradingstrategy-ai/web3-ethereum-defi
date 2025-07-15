"""Enzyme integration tests for guard,

- Check Uniswap v2 access rights

- Check some negative cases for unauthroised transactions
"""

import datetime
import random

import flaky
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.erc20 import prepare_approve
from eth_defi.enzyme.generic_adapter_vault import bind_vault, deploy_generic_adapter_with_guard, deploy_guard, deploy_vault_with_generic_adapter, whitelist_sender_receiver
from eth_defi.enzyme.policy import create_safe_default_policy_configuration_for_generic_adapter, update_adapter_policy
from eth_defi.enzyme.uniswap_v2 import prepare_swap
from eth_defi.enzyme.vault import Vault
from eth_defi.hotwallet import HotWallet
from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil
from eth_defi.terms_of_service.acceptance_message import (
    generate_acceptance_message,
    get_signing_hash,
    sign_terms_of_service,
)
from eth_defi.token import TokenDetails
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, deploy_trading_pair
from eth_defi.usdc.eip_3009 import EIP3009AuthorizationType, make_eip_3009_transfer


@pytest.fixture
def vault_owner(web3, deployer) -> Account:
    return web3.eth.accounts[1]


@pytest.fixture
def asset_manager(web3, deployer) -> Account:
    """Create a LocalAccount user.

    See limitations in `transfer_with_authorization`.
    """
    return web3.eth.accounts[2]


@pytest.fixture
def vault_investor(web3, deployer, usdc: Contract) -> LocalAccount:
    """Create a LocalAccount user.

    See limitations in `transfer_with_authorization`.
    """
    account = Account.create()
    stash = web3.eth.get_balance(deployer)
    tx_hash = web3.eth.send_transaction({"from": deployer, "to": account.address, "value": stash // 2})
    assert_transaction_success_with_explanation(web3, tx_hash)
    usdc.functions.transfer(
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
def enzyme(
    web3,
    deployer,
    mln,
    weth,
    usdc,
    usdc_usd_mock_chainlink_aggregator,
    mln_usd_mock_chainlink_aggregator,
    weth_usd_mock_chainlink_aggregator,
) -> EnzymeDeployment:
    """Deploy Enzyme protocol with few Chainlink feeds mocked with a static price."""
    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        mln,
        weth,
    )

    deployment.add_primitive(
        usdc,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    deployment.add_primitive(
        mln,
        mln_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )

    # We need to set a mock price for ETH/USD otherwise swap test won't pass,
    # as the cumulative slippage tolerancy policy needs to know ETH price
    tx_hash = deployment.contracts.value_interpreter.functions.setEthUsdAggregator(weth_usd_mock_chainlink_aggregator.address).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return deployment


@pytest.fixture()
def hot_wallet(web3):
    _deployer = web3.eth.accounts[0]
    account: LocalAccount = Account.create()
    stash = web3.eth.get_balance(_deployer)
    tx_hash = web3.eth.send_transaction({"from": _deployer, "to": account.address, "value": stash // 2})
    assert_transaction_success_with_explanation(web3, tx_hash)

    hot_wallet = HotWallet(account)
    hot_wallet.sync_nonce(web3)
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))
    return hot_wallet


@pytest.fixture()
def vault(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    enzyme: EnzymeDeployment,
    weth: Contract,
    mln: Contract,
    usdc: Contract,
    terms_of_service: Contract,
    hot_wallet: HotWallet,
) -> Vault:
    """Deploy an Enzyme vault.

    Set up a forge compatible deployer account.

    - GuardV0
    - GuardedGenericAdapter
    - TermsOfService
    - TermedVaultUSDCPaymentForwarder
    """

    return deploy_vault_with_generic_adapter(enzyme, hot_wallet, asset_manager, deployer, usdc, terms_of_service)


@pytest.fixture()
def payment_forwarder(vault: Vault) -> Contract:
    return vault.payment_forwarder


@pytest.fixture()
def uniswap_v2_whitelisted(
    vault: Vault,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
    mln: Contract,
    deployer: str,
) -> UniswapV2Deployment:
    """Whitelist uniswap deployment and WETH-USDC pair on the guard."""
    guard = vault.guard_contract
    guard.functions.whitelistUniswapV2Router(uniswap_v2.router.address, "").transact({"from": vault.deployer_hot_wallet.address})
    guard.functions.whitelistToken(usdc.address, "").transact({"from": vault.deployer_hot_wallet.address})
    guard.functions.whitelistToken(weth.address, "").transact({"from": vault.deployer_hot_wallet.address})
    guard.functions.whitelistToken(mln.address, "").transact({"from": vault.deployer_hot_wallet.address})
    return uniswap_v2


@pytest.fixture()
def mln_weth_pair(web3, deployer, uniswap_v2, weth, mln) -> Contract:
    """mln-weth for 100 ETH at 200$ per token"""
    deposit = 100  # ETH
    pair = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        mln,
        deposit * 10**18,
        int((deposit / (200 / 1600)) * 10**18),
    )
    return pair


def test_enzyme_usdc_payment_forwarder_transfer_with_authorization_and_terms(
    web3: Web3,
    deployer: HexAddress,
    vault: Vault,
    vault_investor: LocalAccount,
    weth: Contract,
    mln: Contract,
    usdc_token: TokenDetails,
    usdc_usd_mock_chainlink_aggregator: Contract,
    payment_forwarder: Contract,
    acceptance_message: str,
    terms_of_service: Contract,
):
    """Buy shares using USDC payment forwader."""

    assert usdc_token.symbol == "USDC"

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
        token=usdc_token,
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

    assert payment_forwarder.functions.amountProxied().call() == 500 * 10**6  # Got sharesarder.address)
    assert vault.get_gross_asset_value() == 500 * 10**6  # Vault has been funded
    assert vault.vault.functions.balanceOf(vault_investor.address).call() == 500 * 10**18  # Got shares
    assert vault.payment_forwarder.address == payment_forwarder.address
    assert vault.payment_forwarder.functions.amountProxied().call() == 500 * 10**6
    assert terms_of_service.functions.canAddressProceed(vault_investor.address).call()


def test_fetch_terms_of_service(web3: Web3, vault: Vault):
    """Resolve terms of service based on the vault."""
    vault2 = Vault.fetch(web3, vault.address, payment_forwarder=vault.payment_forwarder.address)
    assert vault2.terms_of_service_contract is not None


@flaky.flaky
def test_enzyme_guarded_trade_singlehop_uniswap_v2(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    enzyme: EnzymeDeployment,
    vault: Vault,
    vault_investor: LocalAccount,
    weth_token: TokenDetails,
    mln: Contract,
    usdc_token: TokenDetails,
    usdc_usd_mock_chainlink_aggregator: Contract,
    payment_forwarder: Contract,
    acceptance_message: str,
    terms_of_service: Contract,
    uniswap_v2_whitelisted: UniswapV2Deployment,
    weth_usdc_pair: Contract,
):
    """Make a single-hop swap that goes through the call guard."""

    assert vault.is_supported_asset(usdc_token.address)
    assert vault.is_supported_asset(weth_token.address)

    # Sign terms of service
    acceptance_hash, signature = sign_terms_of_service(vault_investor, acceptance_message)

    # The transfer will expire in one hour
    # in the test EVM timeline
    block = web3.eth.get_block("latest")
    valid_before = block["timestamp"] + 3600

    # Construct bounded ContractFunction instance
    # that will transact with MockEIP3009Receiver.deposit()
    # smart contract function.
    bound_func = make_eip_3009_transfer(
        token=usdc_token,
        from_=vault_investor,
        to=payment_forwarder.address,
        func=payment_forwarder.functions.buySharesOnBehalfUsingTransferWithAuthorizationAndTermsOfService,
        value=500 * 10**6,  # 500 USD,
        valid_before=valid_before,
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

    assert vault.get_gross_asset_value() == 500 * 10**6  # Vault has been funded

    # Vault swaps USDC->ETH for both users
    # Buy ETH worth of 200 USD
    prepared_tx = prepare_swap(
        enzyme,
        vault,
        uniswap_v2_whitelisted,
        vault.generic_adapter,
        usdc_token.contract,
        weth_token.contract,
        200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Bought ETH landed in the vault
    assert weth_token.contract.functions.balanceOf(vault.address).call() == pytest.approx(0.12450087262998791 * 10**18)


@flaky.flaky
def test_enzyme_guarded_trade_multihops_uniswap_v2(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    enzyme: EnzymeDeployment,
    vault: Vault,
    vault_investor: LocalAccount,
    weth_token: TokenDetails,
    mln_token: TokenDetails,
    usdc_token: TokenDetails,
    usdc_usd_mock_chainlink_aggregator: Contract,
    payment_forwarder: Contract,
    acceptance_message: str,
    terms_of_service: Contract,
    uniswap_v2_whitelisted: UniswapV2Deployment,
    weth_usdc_pair: Contract,
    mln_weth_pair: Contract,
):
    """Make a multi-hop swap that goes through the call guard."""

    assert vault.is_supported_asset(usdc_token.address)
    assert vault.is_supported_asset(weth_token.address)
    assert vault.is_supported_asset(mln_token.address)

    # Sign terms of service
    acceptance_hash, signature = sign_terms_of_service(vault_investor, acceptance_message)

    # The transfer will expire in one hour
    # in the test EVM timeline
    block = web3.eth.get_block("latest")
    valid_before = block["timestamp"] + 3600

    # Construct bounded ContractFunction instance
    # that will transact with MockEIP3009Receiver.deposit()
    # smart contract function.
    bound_func = make_eip_3009_transfer(
        token=usdc_token,
        from_=vault_investor,
        to=payment_forwarder.address,
        func=payment_forwarder.functions.buySharesOnBehalfUsingTransferWithAuthorizationAndTermsOfService,
        value=500 * 10**6,  # 500 USD,
        valid_before=valid_before,
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

    assert vault.get_gross_asset_value() == 500 * 10**6  # Vault has been funded

    # Vault swaps USDC->ETH->MLN for both users
    # Buy MLN worth of 200 USD
    prepared_tx = prepare_swap(
        enzyme,
        vault,
        uniswap_v2_whitelisted,
        vault.generic_adapter,
        token_in=usdc_token.contract,
        token_out=mln_token.contract,
        token_intermediate=weth_token.contract,
        swap_amount=200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Bought MLN landed in the vault
    assert mln_token.contract.functions.balanceOf(vault.address).call() == pytest.approx(0.991787879885383035 * 10**18)


def test_enzyme_guarded_unauthorised_approve(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    enzyme: EnzymeDeployment,
    vault: Vault,
    usdc_token: TokenDetails,
    usdc_usd_mock_chainlink_aggregator: Contract,
    uniswap_v2_whitelisted: UniswapV2Deployment,
):
    """Asset manager tries to initiate the transfer using GenericAdapter.

    - This is blocked by guard

    - transfer() call site is blocked by default, but we need to test for approve()

    """
    usdc_token.contract.functions.approve(vault.comptroller.address, 500 * 10**6).transact({"from": deployer})
    tx_hash = vault.comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # fmt: off
    prepared_tx = prepare_approve(
        enzyme,
        vault,
        vault.generic_adapter,
        usdc_token.contract,
        asset_manager,
        500 * 10**6,
    )

    with pytest.raises(TransactionAssertionError) as exc_info:
        tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)

    revert_reason = exc_info.value.revert_reason
    assert "Approve address does not match" in revert_reason


def test_enzyme_enable_transfer(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    enzyme: EnzymeDeployment,
    vault: Vault,
    usdc_token: TokenDetails,
    usdc_usd_mock_chainlink_aggregator: Contract,
    uniswap_v2_whitelisted: UniswapV2Deployment,
):
    """Enable transfer for an asset manager."""
    usdc_token.contract.functions.approve(vault.comptroller.address, 500 * 10**6).transact({"from": deployer})
    tx_hash = vault.comptroller.functions.buyShares(500 * 10**6, 1).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # fmt: off
    prepared_tx = prepare_approve(
        enzyme,
        vault,
        vault.generic_adapter,
        usdc_token.contract,
        asset_manager,
        500 * 10**6,
    )

    with pytest.raises(TransactionAssertionError) as exc_info:
        tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)

    revert_reason = exc_info.value.revert_reason
    assert "Approve address does not match" in revert_reason


@pytest.mark.skip(reason="Currently Enzyme does not way to update AdapterPolicy. Instead, the whole vault needs to be reconfigured with 7 days delay.")
def test_enzyme_guarded_trade_singlehop_uniswap_v2_guard_redeploy(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    enzyme: EnzymeDeployment,
    vault: Vault,
    vault_investor: LocalAccount,
    weth_token: TokenDetails,
    mln: Contract,
    usdc_token: TokenDetails,
    usdc_usd_mock_chainlink_aggregator: Contract,
    payment_forwarder: Contract,
    acceptance_message: str,
    terms_of_service: Contract,
    uniswap_v2_whitelisted: UniswapV2Deployment,
    weth_usdc_pair: Contract,
    hot_wallet,
    vault_owner: Account,
):
    """Make a single-hop swap that goes through the call guard."""

    assert vault.is_supported_asset(usdc_token.address)
    assert vault.is_supported_asset(weth_token.address)

    # Sign terms of service
    acceptance_hash, signature = sign_terms_of_service(vault_investor, acceptance_message)

    # The transfer will expire in one hour
    # in the test EVM timeline
    block = web3.eth.get_block("latest")
    valid_before = block["timestamp"] + 3600

    # Construct bounded ContractFunction instance
    # that will transact with MockEIP3009Receiver.deposit()
    # smart contract function.
    bound_func = make_eip_3009_transfer(
        token=usdc_token,
        from_=vault_investor,
        to=payment_forwarder.address,
        func=payment_forwarder.functions.buySharesOnBehalfUsingTransferWithAuthorizationAndTermsOfService,
        value=500 * 10**6,  # 500 USD,
        valid_before=valid_before,
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

    assert vault.get_gross_asset_value() == 500 * 10**6  # Vault has been funded

    hot_wallet.sync_nonce(web3)

    # Adds a new guard to the vault
    guard = deploy_guard(
        web3=web3,
        deployer=hot_wallet,
        asset_manager=asset_manager,
        owner=hot_wallet.address,
        denomination_asset=usdc_token.contract,
        whitelisted_assets=[weth_token, usdc_token],
        etherscan_api_key=None,
        uniswap_v2=True,
    )

    generic_adapter = deploy_generic_adapter_with_guard(
        enzyme,
        hot_wallet,
        guard,
        etherscan_api_key=None,
    )

    # TODO: Fix this so we do not need to fetch Vault twice
    vault = Vault.fetch(
        web3,
        vault.address,
        extra_addresses={
            "comptroller_lib": enzyme.contracts.comptroller_lib.address,
            "allowed_adapters_policy": enzyme.contracts.allowed_adapters_policy.address,
            "generic_adapter": generic_adapter.address,
        },
    )

    bind_vault(
        generic_adapter,
        vault.vault,
        False,
        "",
        hot_wallet,
    )

    policy_configuration = create_safe_default_policy_configuration_for_generic_adapter(
        enzyme,
        generic_adapter,
    )

    # update_adapter_policy(
    #    vault,
    #    generic_adapter,
    #    hot_wallet
    # )

    whitelist_sender_receiver(
        guard,
        hot_wallet,
        allow_receiver=generic_adapter.address,
        allow_sender=vault.address,
    )

    # Vault swaps USDC->ETH for both users
    # Buy ETH worth of 200 USD
    prepared_tx = prepare_swap(
        enzyme,
        vault,
        uniswap_v2_whitelisted,
        vault.generic_adapter,
        usdc_token.contract,
        weth_token.contract,
        200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Bought ETH landed in the vault
    assert weth_token.contract.functions.balanceOf(vault.address).call() == pytest.approx(0.12450087262998791 * 10**18)

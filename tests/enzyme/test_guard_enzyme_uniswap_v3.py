"""Enzyme integration tests for guard

- Check Uniswap v3 access rights

- Check some negative cases for unauthroised transactions
"""
import datetime
import random

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import ChecksumAddress, HexAddress
from terms_of_service.acceptance_message import (
    generate_acceptance_message,
    get_signing_hash,
    sign_terms_of_service,
)
from web3 import Web3
from web3.contract import Contract

from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.uniswap_v3 import prepare_swap
from eth_defi.enzyme.vault import Vault
from eth_defi.middleware import construct_sign_and_send_raw_middleware_anvil
from eth_defi.token import TokenDetails
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.pool import PoolDetails
from eth_defi.uniswap_v3.utils import get_default_tick_range
from eth_defi.usdc.eip_3009 import EIP3009AuthorizationType, make_eip_3009_transfer

POOL_FEE_RAW = 3000


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
def enzyme(web3, deployer, mln, weth, usdc, usdc_usd_mock_chainlink_aggregator, mln_usd_mock_chainlink_aggregator) -> EnzymeDeployment:
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
    return deployment


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
    uniswap_v3: UniswapV3Deployment,
) -> Vault:
    """Deploy an Enzyme vault.

    - GuardV0
    - GuardedGenericAdapter
    - TermsOfService
    - TermedVaultUSDCPaymentForwarder
    """

    deployment = enzyme

    comptroller, vault = deployment.create_new_vault(
        deployer,
        usdc,
    )

    assert comptroller.functions.getDenominationAsset().call() == usdc.address
    assert vault.functions.getTrackedAssets().call() == [usdc.address]

    # asset manager role is the trade executor
    vault.functions.addAssetManagers([asset_manager]).transact({"from": deployer})

    payment_forwarder = deploy_contract(
        web3,
        "TermedVaultUSDCPaymentForwarder.json",
        deployer,
        usdc.address,
        comptroller.address,
        terms_of_service.address,
    )

    guard = deploy_contract(
        web3,
        f"guard/GuardV0.json",
        deployer,
    )
    assert guard.functions.getInternalVersion().call() == 1

    generic_adapter = deploy_contract(
        web3,
        f"GuardedGenericAdapter.json",
        deployer,
        deployment.contracts.integration_manager.address,
        vault.address,
        guard.address,
    )

    # When swap is performed, the tokens will land on the integration contract
    # and this contract must be listed as the receiver.
    # Enzyme will then internally move tokens to its vault from here.
    guard.functions.allowReceiver(generic_adapter.address, "").transact({"from": deployer})

    # Because Enzyme does not pass the asset manager address to through integration manager,
    # we set the vault address itself as asset manager for the guard
    tx_hash = guard.functions.allowSender(vault.address, "").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert generic_adapter.functions.getIntegrationManager().call() == deployment.contracts.integration_manager.address
    assert comptroller.functions.getDenominationAsset().call() == usdc.address
    assert vault.functions.getTrackedAssets().call() == [usdc.address]
    assert vault.functions.canManageAssets(asset_manager).call()
    assert guard.functions.isAllowedSender(vault.address).call()  # vault = asset manager for the guard

    vault = Vault.fetch(
        web3,
        vault_address=vault.address,
        payment_forwarder=payment_forwarder.address,
        generic_adapter_address=generic_adapter.address,
    )
    assert vault.guard_contract.address == guard.address

    # whitelist uniswap v3 and
    guard.functions.whitelistUniswapV3Router(uniswap_v3.swap_router.address, "").transact({"from": deployer})
    guard.functions.whitelistToken(usdc.address, "").transact({"from": deployer})
    guard.functions.whitelistToken(weth.address, "").transact({"from": deployer})

    return vault


@pytest.fixture()
def payment_forwarder(vault: Vault) -> Contract:
    return vault.payment_forwarder


@pytest.fixture()
def uniswap_v3(
    web3: Web3,
    weth: Contract,
    deployer: str,
) -> UniswapV3Deployment:
    """Deploy Uniswap v3."""
    assert web3.eth.get_balance(deployer) > 0
    return deploy_uniswap_v3(web3, deployer, weth=weth, give_weth=500)


@pytest.fixture()
def weth_usdc_pool(web3, uniswap_v3, weth, usdc, deployer) -> Contract:
    """Mock WETH-USDC pool."""

    min_tick, max_tick = get_default_tick_range(POOL_FEE_RAW)

    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=POOL_FEE_RAW,
    )

    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=20_000 * 10**6,  # 20000 USDC liquidity
        amount1=10 * 10**18,  # 10 ETH liquidity
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool


def test_enzyme_guarded_trade_uniswap_v3(
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
    uniswap_v3: UniswapV3Deployment,
    weth_usdc_pool: PoolDetails,
):
    """Make a swap that goes through the call guard."""

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
    tx_hash = bound_func.transact({"from": vault_investor.address})

    # Print out Solidity stack trace if this fails
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert payment_forwarder.functions.amountProxied().call() == 500 * 10**6  # Got shares

    assert vault.get_gross_asset_value() == 500 * 10**6  # Vault has been funded

    # Vault swaps USDC->ETH for both users
    # Buy ETH worth of 200 USD
    prepared_tx = prepare_swap(
        enzyme,
        vault,
        uniswap_v3,
        vault.generic_adapter,
        usdc_token.contract,
        weth_token.contract,
        [3000],
        200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Bought ETH landed in the vault
    assert weth_token.contract.functions.balanceOf(vault.address).call() == pytest.approx(0.09871580343970612 * 10**18)

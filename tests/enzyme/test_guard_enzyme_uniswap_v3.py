"""Enzyme integration tests for guard

- Check Uniswap v3 access rights

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

from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.generic_adapter_vault import deploy_vault_with_generic_adapter
from eth_defi.enzyme.uniswap_v3 import prepare_swap
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
def vault(
    web3: Web3,
    deployer: HexAddress,
    asset_manager: HexAddress,
    enzyme: EnzymeDeployment,
    weth: Contract,
    mln: Contract,
    usdc: Contract,
    terms_of_service: Contract,
) -> Vault:
    """Deploy an Enzyme vault.

    Set up a forge compatible deployer account.

    - GuardV0
    - GuardedGenericAdapter
    - TermsOfService
    - TermedVaultUSDCPaymentForwarder
    """

    _deployer = web3.eth.accounts[0]
    account: LocalAccount = Account.create()
    stash = web3.eth.get_balance(_deployer)
    tx_hash = web3.eth.send_transaction({"from": _deployer, "to": account.address, "value": stash // 2})
    assert_transaction_success_with_explanation(web3, tx_hash)

    hot_wallet = HotWallet(account)
    hot_wallet.sync_nonce(web3)
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

    return deploy_vault_with_generic_adapter(enzyme, hot_wallet, asset_manager, deployer, usdc, terms_of_service)


@pytest.fixture()
def payment_forwarder(vault: Vault) -> Contract:
    return vault.payment_forwarder


@pytest.fixture()
def uniswap_v3(
    web3: Web3,
    vault: Vault,
    weth: Contract,
    usdc: Contract,
    mln: Contract,
    deployer: str,
) -> UniswapV3Deployment:
    """Deploy Uniswap v3."""
    assert web3.eth.get_balance(deployer) > 0
    uniswap = deploy_uniswap_v3(web3, vault.deployer_hot_wallet.address, weth=weth, give_weth=500)

    guard = vault.guard_contract
    guard.functions.whitelistUniswapV3Router(uniswap.swap_router.address, "").transact({"from": vault.deployer_hot_wallet.address})
    guard.functions.whitelistToken(usdc.address, "").transact({"from": vault.deployer_hot_wallet.address})
    guard.functions.whitelistToken(weth.address, "").transact({"from": vault.deployer_hot_wallet.address})
    guard.functions.whitelistToken(mln.address, "").transact({"from": vault.deployer_hot_wallet.address})

    return uniswap


@pytest.fixture()
def weth_usdc_pool(web3, uniswap_v3, weth, usdc, deployer) -> Contract:
    """Mock WETH-USDC pool."""

    min_tick, max_tick = get_default_tick_range(POOL_FEE_RAW)

    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=usdc,
        token1=weth,
        fee=POOL_FEE_RAW,
    )

    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=16_000 * 10**6,  # 16k USDC liquidity
        amount1=10 * 10**18,  # 10 ETH liquidity
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool


@pytest.fixture()
def weth_mln_pool(web3, uniswap_v3, weth, mln, deployer) -> Contract:
    """Mock WETH-MLN pool."""

    min_tick, max_tick = get_default_tick_range(POOL_FEE_RAW)

    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=mln,
        token1=weth,
        fee=POOL_FEE_RAW,
    )

    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=80 * 10**18,  # 10 ETH liquidity
        amount1=10 * 10**18,  # 80 MLN liquidity
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool


@flaky.flaky
def test_enzyme_guarded_trade_singlehop_uniswap_v3(
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
        token_in=usdc_token.contract,
        token_out=weth_token.contract,
        pool_fees=[POOL_FEE_RAW],
        token_in_amount=200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Bought ETH landed in the vault
    assert weth_token.contract.functions.balanceOf(vault.address).call() == pytest.approx(0.123090978678222650 * 10**18)


@flaky.flaky
def test_enzyme_guarded_trade_multihops_uniswap_v3(
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
    uniswap_v3: UniswapV3Deployment,
    weth_usdc_pool: PoolDetails,
    weth_mln_pool: PoolDetails,
):
    """Make a swap that goes through the call guard."""

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
    tx_hash = bound_func.transact({"from": vault_investor.address})

    # Print out Solidity stack trace if this fails
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert payment_forwarder.functions.amountProxied().call() == 500 * 10**6  # Got shares

    assert vault.get_gross_asset_value() == 500 * 10**6  # Vault has been funded

    # Vault swaps USDC->ETH->MLN for both users
    # Buy MLN worth of 200 USD
    prepared_tx = prepare_swap(
        enzyme,
        vault,
        uniswap_v3,
        vault.generic_adapter,
        token_in=usdc_token.contract,
        token_out=mln_token.contract,
        token_intermediate=weth_token.contract,
        pool_fees=[POOL_FEE_RAW, POOL_FEE_RAW],
        token_in_amount=200 * 10**6,  # 200 USD
    )

    tx_hash = prepared_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Bought MLN landed in the vault
    assert mln_token.contract.functions.balanceOf(vault.address).call() == pytest.approx(0.969871220879840482 * 10**18)

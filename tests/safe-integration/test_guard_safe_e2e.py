"""Base mainnet fork based interation tests for TradingStrategyModuleV0 Safe module integration."""

import os

import pytest
from eth_typing import HexAddress
from safe_eth.safe import Safe
from safe_eth.safe.safe import SafeV141
from web3 import Web3
from web3.contract import Contract

from eth_defi.compat import construct_sign_and_send_raw_middleware
from eth_defi.deploy import deploy_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.safe.safe_compat import create_safe_ethereum_client
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v2.deployment import FOREVER_DEADLINE, UniswapV2Deployment, fetch_deployment

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def deployer(web3) -> HexAddress:
    """Role of who can deploy contracts"""
    return web3.eth.accounts[0]


@pytest.fixture()
def asset_manager(web3) -> HexAddress:
    """Role who can perform trades"""
    return web3.eth.accounts[1]


@pytest.fixture()
def attacker_account(web3) -> HexAddress:
    """Unauthorised account, without roles"""
    return web3.eth.accounts[2]


@pytest.fixture()
def safe_deployer_hot_wallet(web3) -> HotWallet:
    """Safe Python library only takes LocalAccount as the input for Safe.create()"""
    hot_wallet = HotWallet.create_for_testing(web3)
    web3.middleware_onion.add(construct_sign_and_send_raw_middleware(hot_wallet.account))
    return hot_wallet


@pytest.fixture()
def usdc_whale() -> HexAddress:
    """Large USDC holder onchain, unlocked in Anvil for testing"""
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"


@pytest.fixture()
def base_usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    )


@pytest.fixture()
def base_weth(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x4200000000000000000000000000000000000006",
    )


@pytest.fixture()
def uniswap_v2(web3) -> UniswapV2Deployment:
    return fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )


@pytest.fixture()
def anvil_base_fork(request, usdc_whale) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[usdc_whale],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    """Create a web3 connector.

    - By default use Anvil forked Base

    - Optionally enable Tenderly testnet with `JSON_RPC_TENDERLY` to debug
      otherwise impossible to debug Gnosis Safe transactions
    """

    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
    else:
        web3 = create_multi_provider_web3(
            anvil_base_fork.json_rpc_url,
            default_http_timeout=(3, 250.0),  # multicall slow, so allow improved timeout
        )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def safe(web3, deployer, safe_deployer_hot_wallet) -> Safe:
    """Deploy a Safe on the forked chain.

    - Use Safe version v1.4.1

    - 1 of 1 multisig

    - safe_deployer set as the sole owner
    """
    ethereum_client = create_safe_ethereum_client(web3)
    owners = [safe_deployer_hot_wallet.address]
    threshold = 1

    # Safe 1.4.1
    # https://help.safe.global/en/articles/40834-verify-safe-creation
    # https://basescan.org/address/0x41675C099F32341bf84BFc5382aF534df5C7461a
    master_copy_address = "0x41675C099F32341bf84BFc5382aF534df5C7461a"

    safe_tx = SafeV141.create(
        ethereum_client,
        safe_deployer_hot_wallet.account,
        master_copy_address,
        owners,
        threshold,
    )
    contract_address = safe_tx.contract_address
    safe = SafeV141(contract_address, ethereum_client)
    retrieved_owners = safe.retrieve_owners()
    assert retrieved_owners == owners
    return safe


@pytest.fixture()
def uniswap_v2_whitelisted_trading_strategy_module(
    web3: Web3,
    safe: Safe,
    safe_deployer_hot_wallet: HotWallet,
    deployer: HexAddress,
    asset_manager: HexAddress,
    uniswap_v2: UniswapV2Deployment,
    base_weth: TokenDetails,
    base_usdc: TokenDetails,
) -> Contract:
    """Enable TradingStrategyModuleV0 that enables trding of a single pair on Uniswap v2.

    - We set up the permissions using the owner role

    - Whitelist only USDC, WETH tokens, single trading pair on Uniswap v2 on Base

    - TradingStrategyModuleV0 is owner by the deployer until the ownership is reliquished at the end
    """

    owner = deployer

    # Deploy guard module
    module = deploy_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        deployer,
        owner,
        safe.address,
    )

    # Enable Safe module
    # Multisig owners can enable the module
    tx = safe.contract.functions.enableModule(module.address).build_transaction(
        {"from": safe_deployer_hot_wallet.address, "gas": 0, "gasPrice": 0},
    )
    safe_tx = safe.build_multisig_tx(safe.address, 0, tx["data"])
    safe_tx.sign(safe_deployer_hot_wallet.private_key.hex())
    tx_hash, tx = safe_tx.execute(
        tx_sender_private_key=safe_deployer_hot_wallet.private_key.hex(),
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Enable asset_manager as the whitelisted trade-executor
    tx_hash = module.functions.allowSender(asset_manager, "Whitelist trade-executor").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Enable safe as the receiver of tokens
    tx_hash = module.functions.allowReceiver(safe.address, "Whitelist Safe as trade receiver").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Whitelist tokens
    module.functions.whitelistToken(base_usdc.address, "Allow USDC").transact({"from": owner})
    module.functions.whitelistToken(base_weth.address, "Allow WETH").transact({"from": owner})

    # Whitelist Uniswap v2
    tx_hash = module.functions.whitelistUniswapV2Router(uniswap_v2.router.address, "Allow Uniswap v2").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Relinquish ownership
    tx_hash = module.functions.transferOwnership(safe.address).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return module


def test_enable_safe_module(
    web3: Web3,
    safe: Safe,
    safe_deployer_hot_wallet: HotWallet,
    deployer: HexAddress,
):
    """Enable TradingStrategyModuleV0 module on Safe."""

    safe_contract = safe.contract

    # Deploy guard module
    module = deploy_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        deployer,
        safe.address,
        safe.address,
    )

    # Multisig owners can enable the module
    tx = safe_contract.functions.enableModule(module.address).build_transaction(
        {"from": safe_deployer_hot_wallet.address, "gas": 0, "gasPrice": 0},
    )
    safe_tx = safe.build_multisig_tx(safe.address, 0, tx["data"])
    safe_tx.sign(safe_deployer_hot_wallet.private_key.hex())
    tx_hash, tx = safe_tx.execute(
        tx_sender_private_key=safe_deployer_hot_wallet.private_key.hex(),
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    modules = safe.retrieve_modules()
    assert modules == [module.address]


def test_swap_through_module_succeed(
    web3: Web3,
    safe: Safe,
    safe_deployer_hot_wallet: HotWallet,
    deployer: HexAddress,
    asset_manager: HexAddress,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    uniswap_v2: UniswapV2Deployment,
    uniswap_v2_whitelisted_trading_strategy_module,
    usdc_whale: HexAddress,
):
    """Perform Uniswap v2 swap using TradingStrategyModuleV0."""

    ts_module = uniswap_v2_whitelisted_trading_strategy_module
    assert safe.retrieve_modules() == [ts_module.address]

    usdc = base_usdc.contract
    weth = base_weth.contract
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(safe.address, usdc_amount).transact({"from": usdc_whale})

    path = [usdc.address, weth.address]

    approve_call = usdc.functions.approve(
        uniswap_v2.router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    tx_hash = ts_module.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert weth.functions.balanceOf(safe.address).call() == 0

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        safe.address,
        FOREVER_DEADLINE,
    )
    target, call_data = encode_simple_vault_transaction(trade_call)
    tx_hash = ts_module.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert weth.functions.balanceOf(safe.address).call() > 0


def test_swap_through_module_revert(
    web3: Web3,
    safe: Safe,
    safe_deployer_hot_wallet: HotWallet,
    deployer: HexAddress,
    asset_manager: HexAddress,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    uniswap_v2: UniswapV2Deployment,
    uniswap_v2_whitelisted_trading_strategy_module,
    usdc_whale: HexAddress,
):
    """Swap reverts (token not approved)"""

    ts_module = uniswap_v2_whitelisted_trading_strategy_module
    assert safe.retrieve_modules() == [ts_module.address]

    usdc = base_usdc.contract
    weth = base_weth.contract
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(safe.address, usdc_amount).transact({"from": usdc_whale})

    path = [usdc.address, weth.address]

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        safe.address,
        FOREVER_DEADLINE,
    )
    target, call_data = encode_simple_vault_transaction(trade_call)

    with pytest.raises(ValueError) as exc_info:
        ts_module.functions.performCall(target, call_data).transact({"from": asset_manager})

    formatted = str(exc_info.value)
    assert "TRANSFER_FROM_FAILED" in formatted, f"Failed: {exc_info.e}\n{exc_info}"


def test_swap_through_module_unauthorised(
    web3: Web3,
    safe: Safe,
    safe_deployer_hot_wallet: HotWallet,
    deployer: HexAddress,
    asset_manager: HexAddress,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    uniswap_v2: UniswapV2Deployment,
    uniswap_v2_whitelisted_trading_strategy_module,
    usdc_whale: HexAddress,
    attacker_account: HexAddress,
):
    """Operation initiated by someone that is not trade-executor"""

    ts_module = uniswap_v2_whitelisted_trading_strategy_module
    assert safe.retrieve_modules() == [ts_module.address]

    usdc = base_usdc.contract
    usdc_amount = 10_000 * 10**6

    approve_call = usdc.functions.approve(
        uniswap_v2.router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    with pytest.raises(ValueError) as e:
        ts_module.functions.performCall(target, call_data).transact({"from": attacker_account})
    assert "validateCall: Sender not allowed" in str(e)

"""Lagoon deposit/withdrawal from other ERC-7540 vaults tests."""

import os
from decimal import Decimal
from typing import cast

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.flow import approve_and_deposit_4626, approve_and_redeem_4626
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.hotwallet import HotWallet
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment, LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.erc_4626.vault_protocol.lagoon.testing import force_lagoon_settle
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.anvil import mine, fork_network_anvil, AnvilLaunch
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation, TransactionAssertionError

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")


@pytest.fixture()
def target_vault_asset_manager():
    """722 capital vault manager"""
    return "0x3B95C7cD4075B72ecbC4559AF99211C2B6591b2E"


@pytest.fixture()
def test_block_number():
    return 41_950_000


@pytest.fixture()
def anvil_base_fork(
    request,
    vault_owner,
    usdc_holder,
    asset_manager,
    valuation_manager,
    target_vault_asset_manager,
    test_block_number,
) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[vault_owner, usdc_holder, asset_manager, valuation_manager, target_vault_asset_manager],
        fork_block_number=test_block_number,
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture
def erc_vault_7540(web3) -> LagoonVault:
    """Pick a random vault which we deposit/withdraw from our own vault"""

    # We leverage deep DeFi expertise and advanced yield optimization strategies—such as delta-neutral positions, basis trading, and leverage loops—across diversified protocols to maximize risk-adjusted returns while capturing upside from early positioning in emerging ecosystems.
    # https://app.lagoon.finance/vault/8453/0xb09f761cb13baca8ec087ac476647361b6314f98
    vault = create_vault_instance(
        web3,
        address="0xb09f761cb13baca8ec087ac476647361b6314f98",
        features={ERC4626Feature.lagoon_like, ERC4626Feature.erc_7540_like},
    )
    return cast(LagoonVault, vault)


def test_lagoon_erc_7540(
    web3: Web3,
    automated_lagoon_vault: LagoonAutomatedDeployment,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    topped_up_asset_manager: HexAddress,
    erc_vault_7540: ERC4626Vault,
    deployer_hot_wallet: HotWallet,
    multisig_owners: list[HexAddress],
    new_depositor: HexAddress,
    asset_manager: HexAddress,
    target_vault_asset_manager: HexAddress,
):
    """Perform a deposit/withdrawal into another ERC-7540 vault from Lagoon vault.


    - Check TradingStrategyModuleV0 is configured and guard are working

    """

    #
    # 1. Deploy new Lagoon vault where the target vault is whitelisted on the guard
    #

    chain_id = web3.eth.chain_id
    asset_manager = topped_up_asset_manager
    assert asset_manager.startswith("0x")
    assert target_vault_asset_manager.startswith("0x")
    usdc = base_usdc
    depositor = new_depositor
    target_vault = erc_vault_7540

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_hot_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,
        erc_4626_vaults=[target_vault],
    )

    #
    # 2. Fund our vault
    #

    vault = deploy_info.vault
    assert not vault.trading_strategy_module.functions.anyAsset().call()

    # We need to do the initial valuation at value 0
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit 9.00 USDC into the vault
    usdc_amount = 9 * 10**6
    tx_hash = usdc.contract.functions.approve(vault.address, usdc_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, usdc_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # We need to do the initial valuation at value 0
    valuation = Decimal(0)
    bound_func = vault.post_new_valuation(valuation)
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Settle deposit queue 9 USDC -> 0 USDC
    settle_func = vault.settle_via_trading_strategy_module(valuation)
    tx_hash = settle_func.transact(
        {
            "from": asset_manager,
            "gas": 1_000_000,
        }
    )
    assert_transaction_success_with_explanation(
        web3,
        tx_hash,
        func=settle_func,
        tracing=True,
    )

    #
    # 3. Request deposit into the target vault
    #

    deposit_manager = target_vault.deposit_manager

    # Request deposit to the target vault from our vault
    usdc_amount = Decimal(9)
    our_address = vault.safe_address
    deposit_ticket = deposit_manager.create_deposit_request(our_address, amount=usdc_amount)
    fn_calls = [
        usdc.approve(target_vault.vault_address, usdc_amount),
        deposit_ticket.funcs[0],
    ]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # Target vault settles
    force_lagoon_settle(
        target_vault,
        target_vault_asset_manager,
    )

    #
    # 4. Finish deposit request
    #

    fn_calls = [deposit_manager.finish_deposit(deposit_ticket)]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # We got our shares
    share_token = target_vault.share_token
    share_amount = share_token.fetch_balance_of(our_address)
    assert share_amount > 0

    #
    # 5. Request redeem
    #

    assert deposit_manager.can_create_redemption_request(our_address)
    redeem_ticket = deposit_manager.create_redemption_request(
        our_address,
        shares=share_amount,
    )
    fn_calls = [
        share_token.approve(target_vault.vault_address, usdc_amount),
        redeem_ticket.funcs[0],
    ]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # Target vault settles
    force_lagoon_settle(
        target_vault,
        target_vault_asset_manager,
    )

    #
    # 7. Finish redeem
    #

    fn_calls = [deposit_manager.finish_redemption(redeem_ticket)]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)


def test_lagoon_erc_7540_malicious_redemption(
    web3: Web3,
    automated_lagoon_vault: LagoonAutomatedDeployment,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    topped_up_asset_manager: HexAddress,
    erc_vault_7540: ERC4626Vault,
    deployer_hot_wallet: HotWallet,
    multisig_owners: list[HexAddress],
    new_depositor: HexAddress,
    asset_manager: HexAddress,
    target_vault_asset_manager: HexAddress,
):
    """Same as above, but change the redemption address to the asset manager's own address.

    - Try to steal assets at redemption
    """

    #
    # 1. Deploy new Lagoon vault where the target vault is whitelisted on the guard
    #

    chain_id = web3.eth.chain_id
    asset_manager = topped_up_asset_manager
    assert asset_manager.startswith("0x")
    assert target_vault_asset_manager.startswith("0x")
    usdc = base_usdc
    depositor = new_depositor
    target_vault = erc_vault_7540

    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="Example",
        symbol="EXA",
    )

    deploy_info = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer_hot_wallet,
        asset_manager=asset_manager,
        parameters=parameters,
        safe_owners=multisig_owners,
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,
        erc_4626_vaults=[target_vault],
    )

    #
    # 2. Fund our vault
    #

    vault = deploy_info.vault
    assert not vault.trading_strategy_module.functions.anyAsset().call()

    # We need to do the initial valuation at value 0
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Deposit 9.00 USDC into the vault
    usdc_amount = 9 * 10**6
    tx_hash = usdc.contract.functions.approve(vault.address, usdc_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_func = vault.request_deposit(depositor, usdc_amount)
    tx_hash = deposit_func.transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # We need to do the initial valuation at value 0
    valuation = Decimal(0)
    bound_func = vault.post_new_valuation(valuation)
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Settle deposit queue 9 USDC -> 0 USDC
    settle_func = vault.settle_via_trading_strategy_module(valuation)
    tx_hash = settle_func.transact(
        {
            "from": asset_manager,
            "gas": 1_000_000,
        }
    )
    assert_transaction_success_with_explanation(
        web3,
        tx_hash,
        func=settle_func,
        tracing=True,
    )

    #
    # 3. Request deposit into the target vault
    #

    deposit_manager = target_vault.deposit_manager

    # Request deposit to the target vault from our vault
    usdc_amount = Decimal(9)
    our_address = vault.safe_address
    deposit_ticket = deposit_manager.create_deposit_request(our_address, amount=usdc_amount)
    fn_calls = [
        usdc.approve(target_vault.vault_address, usdc_amount),
        deposit_ticket.funcs[0],
    ]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # Target vault settles
    force_lagoon_settle(
        target_vault,
        target_vault_asset_manager,
    )

    #
    # 4. Finish deposit request
    #

    fn_calls = [deposit_manager.finish_deposit(deposit_ticket)]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # We got our shares
    share_token = target_vault.share_token
    share_amount = share_token.fetch_balance_of(our_address)
    assert share_amount > 0

    #
    # 5. Request redeem
    #

    assert deposit_manager.can_create_redemption_request(our_address)
    redeem_ticket = deposit_manager.create_redemption_request(
        our_address,
        shares=share_amount,
    )
    fn_calls = [
        share_token.approve(target_vault.vault_address, usdc_amount),
        redeem_ticket.funcs[0],
    ]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # Target vault settles
    force_lagoon_settle(
        target_vault,
        target_vault_asset_manager,
    )

    #
    # 7. Finish redeem
    #

    redeem_ticket.to = asset_manager  # Try to steal to the asset manager address

    fn_calls = [deposit_manager.finish_redemption(redeem_ticket)]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        with pytest.raises(TransactionAssertionError):
            tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
            assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

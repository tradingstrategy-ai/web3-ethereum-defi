"""Lagoon deposit/withdrawal from other ERC-7540 vaults tests."""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3
import flaky

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.gains.testing import force_next_gains_epoch
from eth_defi.gains.vault import GainsVault
from eth_defi.hotwallet import HotWallet
from eth_defi.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.provider.anvil import mine, fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN, USDC_WHALE, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")


@pytest.fixture()
def anvil_arbitrum_fork(request, asset_manager) -> AnvilLaunch:
    """Reset write state between tests"""

    usdc_whale = USDC_WHALE[42161]

    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=375_216_652,
        unlocked_addresses=[usdc_whale, asset_manager],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@pytest.fixture()
def topped_up_asset_manager(web3, asset_manager) -> HexAddress:
    # Topped up with some ETH
    tx_hash = web3.eth.send_transaction(
        {
            "to": asset_manager,
            "from": web3.eth.accounts[0],
            "value": 9 * 10**18,
        }
    )
    assert_transaction_success_with_explanation(web3, tx_hash)
    return asset_manager


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    usdc = fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[42161],
    )
    return usdc


@pytest.fixture
def gains_vault(web3) -> GainsVault:
    """gTrade USDC vault on Arbitrum"""
    vault_address = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, GainsVault)
    return vault


@pytest.fixture()
def new_depositor(web3, usdc) -> HexAddress:
    """User with some USDC ready to deposit.

    - Start with 500 USDC
    """
    new_depositor = web3.eth.accounts[5]
    usdc_holder = USDC_WHALE[42161]
    tx_hash = usdc.transfer(new_depositor, Decimal(500)).transact({"from": usdc_holder, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return new_depositor


#
@flaky.flaky
def test_lagoon_gains(
    web3: Web3,
    usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    gains_vault: GainsVault,
    deployer_hot_wallet: HotWallet,
    multisig_owners: list[HexAddress],
    new_depositor: HexAddress,
    asset_manager: HexAddress,
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
    depositor = new_depositor
    target_vault = gains_vault

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
        from_the_scratch=True,
        use_forge=True,
    )

    #
    # 2. Fund our vault
    #

    vault = deploy_info.vault
    our_address = vault.safe_address
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
    # 3. Deposit into the target vault
    #

    deposit_manager = target_vault.deposit_manager

    assert deposit_manager.can_create_deposit_request(our_address)

    # Request deposit to the target vault from our vault
    usdc_amount = Decimal(9)

    deposit_request = deposit_manager.create_deposit_request(our_address, amount=usdc_amount)
    fn_calls = [
        usdc.approve(target_vault.vault_address, usdc_amount),
        deposit_request.funcs[0],
    ]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # We got our shares
    share_token = target_vault.share_token
    share_amount = share_token.fetch_balance_of(our_address)
    assert share_amount > 0

    #
    #
    #

    # 0. Clear epoch
    force_next_gains_epoch(
        target_vault,
        asset_manager,
    )

    #
    # 5. Request redeem
    #

    assert deposit_manager.can_create_redemption_request(our_address)

    redeem_request = deposit_manager.create_redemption_request(
        our_address,
        shares=share_amount,
    )
    fn_calls = [
        share_token.approve(target_vault.vault_address, usdc_amount),
        redeem_request.funcs[0],
    ]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    redemption_ticket = redeem_request.parse_redeem_transaction([tx_hash])

    # Cannot redeem yet, need to wait for the next epoch
    assert deposit_manager.can_finish_redeem(redemption_ticket) is False

    # 3. Move forward few epochs where our request unlocks
    for i in range(0, 3):
        force_next_gains_epoch(
            target_vault,
            asset_manager,
        )

    assert target_vault.fetch_current_epoch() >= 200

    # Cannot redeem yet, need to wait for the next epoch
    assert deposit_manager.can_finish_redeem(redemption_ticket) is True
    #
    # 7. Finish redeem
    #

    fn_calls = [deposit_manager.finish_redemption(redemption_ticket)]
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

"""Lagoon deposit/withdrawal from other ERC-4626 vaults tests."""

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
from eth_defi.provider.anvil import mine
from eth_defi.token import TokenDetails, USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation


@pytest.fixture
def erc4626_vault(web3) -> ERC4626Vault:
    """Pick a random vault which we deposit/withdraw from our own vault"""

    # Harvest USDC Autopilot on IPOR on Base
    # https://app.ipor.io/fusion/base/0x0d877dc7c8fa3ad980dfdb18b48ec9f8768359c4
    # (ChainId.base, "0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4".lower()),

    vault = create_vault_instance(
        web3,
        address="0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4",
        features={ERC4626Feature.ipor_like},
    )
    return cast(ERC4626Vault, vault)


def test_lagoon_erc_4626(
    web3: Web3,
    automated_lagoon_vault: LagoonAutomatedDeployment,
    base_usdc: TokenDetails,
    base_weth: TokenDetails,
    topped_up_asset_manager: HexAddress,
    erc4626_vault: ERC4626Vault,
    deployer_hot_wallet: HotWallet,
    multisig_owners,
    new_depositor: HexAddress,
):
    """Perform a deposit/withdrawal swap for ERC-4626 vault from Lagoon.

    - Check TradingStrategyModuleV0 is configured
    """

    chain_id = web3.eth.chain_id
    asset_manager = topped_up_asset_manager
    usdc = base_usdc
    depositor = new_depositor

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
        erc_4626_vaults=[erc4626_vault],
    )

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

    # Check we have money for the swap
    swap_amount = usdc_amount // 2
    assert usdc.contract.functions.balanceOf(vault.safe_address).call() >= swap_amount

    # Approve and deposit into the vault
    usdc_amount = Decimal(9)
    fn_calls = approve_and_deposit_4626(
        vault=erc4626_vault,
        amount=usdc_amount,
        from_=vault.address,
        check_enough_token=False,
        receiver=vault.safe_address,
    )

    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

    # Approve and withdraw into our Lagoon vault from the IPOR vault we use for trading

    # We need to skip time or the IPOR redeem will revert
    mine(web3, increase_timestamp=3600)

    share_amount = erc4626_vault.share_token.fetch_balance_of(vault.safe_address)
    assert share_amount > 0
    fn_calls = approve_and_redeem_4626(
        vault=erc4626_vault,
        amount=share_amount,
        from_=vault.safe_address,
        check_enough_token=False,
    )

    # | Error    | ERC4626ExceededMaxRedeem(address,uint256,uint256)   | 0xb94abeec
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)

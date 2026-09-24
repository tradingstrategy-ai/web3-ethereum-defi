"""Fixed-Base-fork deployment tests for Lagoon settlement-window budgets."""

from decimal import Decimal

import pytest
from eth_abi import encode
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    DEFAULT_LAGOON_SETTLEMENT_WINDOW,
    LAGOON_SETTLEMENT_LIMIT_INTERNAL_VERSION,
    LagoonAutomatedDeployment,
    LagoonConfig,
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import mine
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.safe.execute import execute_safe_tx
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation

pytestmark = pytest.mark.xdist_group("fork:base:lagoon-v05")


@pytest.mark.parametrize(
    ("config_overrides", "expected_error"),
    (
        ({"satellite_chain": True}, "satellite chain"),
        ({"vault_abi": "lagoon/Vault.json"}, "stock Lagoon v0.5 vault ABI"),
        ({"vault_abi": "lagoon/v0.6.0/Vault.json"}, "stock Lagoon v0.5 vault ABI"),
    ),
)
def test_lagoon_config_rejects_unenforceable_settlement_limit(
    config_overrides: dict[str, object],
    expected_error: str,
) -> None:
    """Reject configurations whose topology cannot enforce settlement safety."""
    parameters = LagoonDeploymentParameters(underlying=USDC_NATIVE_TOKEN[8453], name="Unsupported capped Lagoon", symbol="CAP")

    with pytest.raises(AssertionError, match=expected_error):
        LagoonConfig(
            parameters=parameters,
            asset_manager="0x0000000000000000000000000000000000000001",
            safe_owners=["0x0000000000000000000000000000000000000002"],
            safe_threshold=1,
            max_settlement_amount=Decimal(1),
            **config_overrides,
        )


def test_lagoon_config_rejects_zero_settlement_window() -> None:
    """Reject an enabled budget without a positive settlement window."""
    parameters = LagoonDeploymentParameters(underlying=USDC_NATIVE_TOKEN[8453], name="Unsafe Lagoon window", symbol="WINDOW")

    with pytest.raises(AssertionError, match="settlement_window must be positive"):
        LagoonConfig(
            parameters=parameters,
            asset_manager="0x0000000000000000000000000000000000000001",
            safe_owners=["0x0000000000000000000000000000000000000002"],
            safe_threshold=1,
            max_settlement_amount=Decimal(1),
            settlement_window=0,
        )


def _deploy_capped_vault(
    web3: Web3,
    deployer: HotWallet,
    asset_manager: HexAddress,
    safe_owner: HexAddress,
    max_settlement_amount: Decimal,
    *,
    use_config_api: bool,
    settlement_window: int = DEFAULT_LAGOON_SETTLEMENT_WINDOW,
) -> LagoonAutomatedDeployment:
    """Deploy a stock Lagoon v0.5 vault with its settlement budget enabled."""
    parameters = LagoonDeploymentParameters(underlying=USDC_NATIVE_TOKEN[web3.eth.chain_id], name="Capped Lagoon", symbol="CAP")
    deployment_kwargs = {
        "asset_manager": asset_manager,
        "parameters": parameters,
        "safe_owners": [safe_owner],
        "safe_threshold": 1,
        "uniswap_v2": None,
        "uniswap_v3": None,
        "max_settlement_amount": max_settlement_amount,
        "settlement_window": settlement_window,
    }
    if use_config_api:
        return deploy_automated_lagoon_vault(web3=web3, deployer=deployer, config=LagoonConfig(**deployment_kwargs))
    return deploy_automated_lagoon_vault(web3=web3, deployer=deployer, **deployment_kwargs)


def _request_deposit(deployment: LagoonAutomatedDeployment, token: TokenDetails, depositor: HexAddress, raw_amount: int) -> None:
    """Place an exact raw USDC request into a Lagoon pending-deposit Silo."""
    web3 = deployment.vault.web3
    tx_hash = token.contract.functions.approve(deployment.vault.address, raw_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = deployment.vault.request_deposit(depositor, raw_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)


def _settle_deposit(deployment: LagoonAutomatedDeployment, asset_manager: HexAddress, valuation: Decimal) -> None:
    """Post a valuation and settle the complete pending deposit epoch through the module."""
    web3 = deployment.vault.web3
    tx_hash = deployment.vault.post_new_valuation(valuation).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = deployment.vault.settle_via_trading_strategy_module(valuation).transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)


def test_lagoon_v05_settlement_window_accumulates_and_resets(
    web3: Web3,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    new_depositor: HexAddress,
    deployer_hot_wallet: HotWallet,
) -> None:
    """Wire the public deployment API to an accumulating, fixed settlement window."""
    asset_manager = topped_up_asset_manager
    deployment = _deploy_capped_vault(
        web3,
        deployer_hot_wallet,
        asset_manager,
        web3.eth.accounts[2],
        Decimal(5_000),
        use_config_api=True,
    )
    vault = deployment.vault
    module = deployment.trading_strategy_module
    assert module.functions.getInternalVersion().call() == LAGOON_SETTLEMENT_LIMIT_INTERNAL_VERSION
    assert module.functions.getTradingStrategyModuleVersion().call() == "v0.6"

    cap_raw = base_usdc.convert_to_raw(Decimal(5_000))
    assert module.functions.getLagoonSettlementSafetyConfig(vault.address).call() == [
        True,
        True,
        base_usdc.address,
        vault.silo_address,
        cap_raw,
        DEFAULT_LAGOON_SETTLEMENT_WINDOW,
        0,
        0,
    ]

    # Empty settlement neither consumes budget nor creates a window.
    _settle_deposit(deployment, asset_manager, Decimal(0))
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [DEFAULT_LAGOON_SETTLEMENT_WINDOW, 0, 0]

    first_raw = base_usdc.convert_to_raw(Decimal(1))
    _request_deposit(deployment, base_usdc, new_depositor, first_raw)
    _settle_deposit(deployment, asset_manager, Decimal(0))
    first_window = module.functions.getLagoonSettlementCooldownConfig(vault.address).call()
    assert first_window[0] == DEFAULT_LAGOON_SETTLEMENT_WINDOW
    assert first_window[1] == first_raw
    assert first_window[2] > 0
    tx_hash = vault.finalise_deposit(new_depositor).transact({"from": new_depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    second_raw = base_usdc.convert_to_raw(Decimal(20))
    _request_deposit(deployment, base_usdc, new_depositor, second_raw)
    _settle_deposit(deployment, asset_manager, Decimal(1))
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [
        DEFAULT_LAGOON_SETTLEMENT_WINDOW,
        first_raw + second_raw,
        first_window[2],
    ]
    assert base_usdc.fetch_raw_balance_of(vault.safe_address) == first_raw + second_raw
    tx_hash = vault.finalise_deposit(new_depositor).transact({"from": new_depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # At expiry the following non-zero settlement opens a fresh budget.
    mine(web3, increase_timestamp=DEFAULT_LAGOON_SETTLEMENT_WINDOW)
    _request_deposit(deployment, base_usdc, new_depositor, second_raw)
    _settle_deposit(deployment, asset_manager, Decimal(21))
    refreshed_window = module.functions.getLagoonSettlementCooldownConfig(vault.address).call()
    assert refreshed_window[1] == second_raw
    assert refreshed_window[2] > first_window[2]


def test_lagoon_v05_over_budget_module_call_reverts_and_safe_can_recover(
    web3: Web3,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    new_depositor: HexAddress,
    deployer_hot_wallet: HotWallet,
) -> None:
    """Keep direct Safe governance outside the operator's settlement budget."""
    asset_manager = topped_up_asset_manager
    deployment = _deploy_capped_vault(
        web3,
        deployer_hot_wallet,
        asset_manager,
        web3.eth.accounts[2],
        Decimal(8),
        use_config_api=False,
        settlement_window=3_600,
    )
    vault = deployment.vault
    module = deployment.trading_strategy_module
    deposit_raw = base_usdc.convert_to_raw(Decimal(9))
    _request_deposit(deployment, base_usdc, new_depositor, deposit_raw)
    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    settle_call = vault.settle_via_trading_strategy_module(Decimal(0))
    expected_error = Web3.keccak(text="LagoonSettlementWindowLimitExceeded(uint256,uint256,uint256)")[:4] + encode(["uint256", "uint256", "uint256"], [0, deposit_raw, base_usdc.convert_to_raw(Decimal(8))])
    with pytest.raises(ExtraValueError) as exc_info:
        settle_call.call({"from": asset_manager})
    assert exc_info.value.args[0]["data"] == Web3.to_hex(expected_error)
    assert vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number) == Decimal(9)
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [3_600, 0, 0]

    direct_settlement = vault.vault_contract.functions.settleDeposit(0)
    safe_tx = vault.safe.build_multisig_tx(vault.address, 0, bytes.fromhex(direct_settlement._encode_transaction_data().removeprefix("0x")))
    private_key = deployer_hot_wallet.account._private_key.hex()
    safe_tx.sign(private_key)
    tx_hash, _ = execute_safe_tx(safe_tx, tx_sender_private_key=private_key, tx_gas=1_500_000, hot_wallet=deployer_hot_wallet)
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number) == 0
    assert base_usdc.fetch_raw_balance_of(vault.safe_address) == deposit_raw
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [3_600, 0, 0]

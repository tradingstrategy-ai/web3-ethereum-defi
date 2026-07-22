"""Anvil mainnet-fork tests for Lagoon v0.5 settlement safety controls."""

from decimal import Decimal

import pytest
from eth_abi import decode, encode
from eth_typing import HexAddress
from web3 import Web3
from web3._utils.events import EventLogErrorFlags

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    DEFAULT_LAGOON_SETTLEMENT_COOLDOWN,
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
    """Reject public configurations whose deployment topology cannot enforce the cap.

    :param config_overrides:
        Unsupported public configuration fields to apply.
    :param expected_error:
        Diagnostic fragment explaining why enforcement is unavailable.
    """
    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[8453],
        name="Unsupported capped Lagoon",
        symbol="CAP",
    )

    with pytest.raises(AssertionError, match=expected_error):
        LagoonConfig(
            parameters=parameters,
            asset_manager="0x0000000000000000000000000000000000000001",
            safe_owners=["0x0000000000000000000000000000000000000002"],
            safe_threshold=1,
            max_settlement_amount=Decimal(1),
            **config_overrides,
        )


def test_lagoon_config_rejects_zero_settlement_cooldown() -> None:
    """Reject an amount cap without a positive asset-manager cooldown."""
    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[8453],
        name="Unsafe Lagoon cooldown",
        symbol="COOL",
    )

    with pytest.raises(AssertionError, match="settlement_cooldown must be positive"):
        LagoonConfig(
            parameters=parameters,
            asset_manager="0x0000000000000000000000000000000000000001",
            safe_owners=["0x0000000000000000000000000000000000000002"],
            safe_threshold=1,
            max_settlement_amount=Decimal(1),
            settlement_cooldown=0,
        )


def _deploy_capped_vault(
    web3: Web3,
    deployer: HotWallet,
    asset_manager: HexAddress,
    safe_owner: HexAddress,
    max_settlement_amount: Decimal,
    *,
    use_config_api: bool,
    settlement_cooldown: int = DEFAULT_LAGOON_SETTLEMENT_COOLDOWN,
) -> LagoonAutomatedDeployment:
    """Deploy a stock Lagoon v0.5 vault with settlement safety enabled.

    A one-owner Safe keeps the governance recovery path deterministic in the
    test: the deployment hot wallet remains a Safe owner and can submit a
    direct settlement without going through the asset-manager module.

    :param web3:
        Base mainnet Anvil fork connection.
    :param deployer:
        Hot wallet funding and signing the deployment.
    :param asset_manager:
        Address permitted to settle through TradingStrategyModuleV0.
    :param safe_owner:
        Additional Safe owner address.
    :param max_settlement_amount:
        Asset-manager safety limit in human-readable USDC units.
    :param use_config_api:
        Use :class:`LagoonConfig` when true and the backwards-compatible direct
        deployment keyword when false. The two end-to-end tests exercise both
        publicly supported deployment APIs without adding another deployment.
    :param settlement_cooldown:
        Minimum seconds between successful asset-manager settlements.
    :return:
        Complete Lagoon deployment.
    """
    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[web3.eth.chain_id],
        name="Capped Lagoon",
        symbol="CAP",
    )
    deployment_kwargs = {
        "asset_manager": asset_manager,
        "parameters": parameters,
        "safe_owners": [safe_owner],
        "safe_threshold": 1,
        "uniswap_v2": None,
        "uniswap_v3": None,
        "max_settlement_amount": max_settlement_amount,
        "settlement_cooldown": settlement_cooldown,
    }
    if use_config_api:
        return deploy_automated_lagoon_vault(
            web3=web3,
            deployer=deployer,
            config=LagoonConfig(**deployment_kwargs),
        )

    return deploy_automated_lagoon_vault(
        web3=web3,
        deployer=deployer,
        **deployment_kwargs,
    )


def _request_deposit(
    deployment: LagoonAutomatedDeployment,
    token: TokenDetails,
    depositor: HexAddress,
    raw_amount: int,
) -> None:
    """Place an exact raw USDC amount in the Lagoon pending deposit Silo.

    :param deployment:
        Vault deployment receiving the request.
    :param token:
        Vault denomination token.
    :param depositor:
        Funded depositor address.
    :param raw_amount:
        Deposit amount in raw token units.
    """
    web3 = deployment.vault.web3
    tx_hash = token.contract.functions.approve(deployment.vault.address, raw_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = deployment.vault.request_deposit(depositor, raw_amount).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)


def test_lagoon_v05_settlement_safety_accepts_after_cooldown_and_rejects_repeat(
    web3: Web3,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    new_depositor: HexAddress,
    deployer_hot_wallet: HotWallet,
) -> None:
    """Accept below-cap settlements but reject repeats until cooldown expiry."""
    asset_manager = topped_up_asset_manager
    cap = Decimal(9)
    cap_raw = base_usdc.convert_to_raw(cap)
    deployment = _deploy_capped_vault(
        web3,
        deployer_hot_wallet,
        asset_manager,
        web3.eth.accounts[2],
        cap,
        use_config_api=True,
    )
    vault = deployment.vault
    module = deployment.trading_strategy_module
    lagoon_events = get_deployed_contract(web3, "guard/LagoonLib.json", module.address)

    assert module.functions.getInternalVersion().call() == 3
    assert module.functions.getTradingStrategyModuleVersion().call() == "v0.5"
    assert module.functions.getLagoonSettlementConfig(vault.address).call() == [
        True,
        True,
        base_usdc.address,
        vault.silo_address,
        cap_raw,
    ]
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [
        DEFAULT_LAGOON_SETTLEMENT_COOLDOWN,
        0,
        0,
    ]

    # The deployment topology is deliberately one vault per guard and Safe.
    # Reconfiguration may update this vault's cap, but must not leave stale
    # permissions behind by silently replacing it with another vault.
    replacement_vault = web3.eth.accounts[9]
    already_configured_selector = Web3.keccak(text="LagoonVaultAlreadyConfigured(address,address)")[:4]
    with pytest.raises(ExtraValueError) as exc_info:
        module.functions.whitelistLagoon(
            replacement_vault,
            "Unexpected replacement",
        ).call({"from": vault.safe_address})
    revert_data = Web3.to_bytes(hexstr=exc_info.value.args[0]["data"])
    assert revert_data[:4] == already_configured_selector
    configured_vault, requested_vault = decode(["address", "address"], revert_data[4:])
    assert Web3.to_checksum_address(configured_vault) == vault.address
    assert Web3.to_checksum_address(requested_vault) == replacement_vault

    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    _request_deposit(deployment, base_usdc, new_depositor, cap_raw)
    assert vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number) == cap
    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    settle_call = vault.settle_via_trading_strategy_module(Decimal(0))
    tx_hash = settle_call.transact({"from": asset_manager, "gas": 1_000_000})
    receipt = assert_transaction_success_with_explanation(web3, tx_hash)

    settlement_events = lagoon_events.events.LagoonSettlementValidated().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
    assert len(settlement_events) == 1
    assert settlement_events[0]["args"]["depositAssets"] == cap_raw
    assert settlement_events[0]["args"]["redeemAssets"] == 0
    assert settlement_events[0]["args"]["grossSettlementAmount"] == cap_raw
    cooldown_events = lagoon_events.events.LagoonSettlementCooldownStarted().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
    assert len(cooldown_events) == 1
    first_settlement_timestamp = int(web3.eth.get_block(receipt["blockNumber"])["timestamp"])
    assert cooldown_events[0]["args"]["settlementTimestamp"] == first_settlement_timestamp
    assert cooldown_events[0]["args"]["nextSettlementTimestamp"] == first_settlement_timestamp + DEFAULT_LAGOON_SETTLEMENT_COOLDOWN
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [
        DEFAULT_LAGOON_SETTLEMENT_COOLDOWN,
        first_settlement_timestamp,
        first_settlement_timestamp + DEFAULT_LAGOON_SETTLEMENT_COOLDOWN,
    ]
    assert vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number) == 0
    assert base_usdc.fetch_raw_balance_of(vault.safe_address) == cap_raw

    tx_hash = vault.finalise_deposit(new_depositor).transact({"from": new_depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    redeem_amount = Decimal(4)
    redeem_raw = vault.share_token.convert_to_raw(redeem_amount)
    tx_hash = vault.request_redeem(new_depositor, redeem_raw).transact({"from": new_depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number) == redeem_amount
    tx_hash = vault.post_new_valuation(cap).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    settle_call = vault.settle_via_trading_strategy_module(cap)
    vault_assets_before = base_usdc.fetch_raw_balance_of(vault.address)

    # A second below-cap settlement would pass the amount check, but must fail
    # before Safe execution while the asset-manager safety cooldown is active.
    cooldown_selector = Web3.keccak(text="LagoonSettlementCooldownActive(uint256,uint256)")[:4]
    with pytest.raises(ExtraValueError) as exc_info:
        settle_call.call({"from": asset_manager})
    revert_data = Web3.to_bytes(hexstr=exc_info.value.args[0]["data"])
    assert revert_data[:4] == cooldown_selector
    current_timestamp, next_settlement_timestamp = decode(["uint256", "uint256"], revert_data[4:])
    assert current_timestamp < next_settlement_timestamp
    assert next_settlement_timestamp == first_settlement_timestamp + DEFAULT_LAGOON_SETTLEMENT_COOLDOWN
    assert vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number) == redeem_amount

    tx_hash = settle_call.transact({"from": asset_manager, "gas": 1_000_000})
    cooldown_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert cooldown_receipt["status"] == 0
    assert vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number) == redeem_amount
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [
        DEFAULT_LAGOON_SETTLEMENT_COOLDOWN,
        first_settlement_timestamp,
        first_settlement_timestamp + DEFAULT_LAGOON_SETTLEMENT_COOLDOWN,
    ]

    mine(web3, increase_timestamp=DEFAULT_LAGOON_SETTLEMENT_COOLDOWN)
    tx_hash = settle_call.transact({"from": asset_manager, "gas": 1_000_000})
    receipt = assert_transaction_success_with_explanation(web3, tx_hash)
    vault_assets_after = base_usdc.fetch_raw_balance_of(vault.address)

    settlement_events = lagoon_events.events.LagoonSettlementValidated().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
    assert len(settlement_events) == 1
    assert settlement_events[0]["args"]["depositAssets"] == 0
    # Lagoon fee accrual uses the generated Anvil block timestamp, so share to
    # asset rounding can vary by one raw unit of USDC between test processes. Check
    # the exact onchain balance delta observed in this settlement instead.
    redeem_assets = vault_assets_after - vault_assets_before
    assert settlement_events[0]["args"]["redeemAssets"] == redeem_assets
    assert settlement_events[0]["args"]["grossSettlementAmount"] == redeem_assets
    assert 0 < redeem_assets <= cap_raw
    assert vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number) == 0

    # A combined settlement must use gross movement rather than the Safe's
    # smaller net balance change: 8 USDC deposit + 2 USDC redemption > 9 cap.
    combined_deposit = Decimal(8)
    combined_redeem = Decimal(2)
    _request_deposit(deployment, base_usdc, new_depositor, base_usdc.convert_to_raw(combined_deposit))
    tx_hash = vault.request_redeem(new_depositor, vault.share_token.convert_to_raw(combined_redeem)).transact({"from": new_depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = vault.post_new_valuation(Decimal(5)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Move beyond the second successful settlement's cooldown so this branch
    # reaches and exercises the independent gross-amount rejection.
    mine(web3, increase_timestamp=DEFAULT_LAGOON_SETTLEMENT_COOLDOWN)

    combined_state_before = {
        "pending_deposit": vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number),
        "pending_redemption": vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number),
        "safe_assets": base_usdc.fetch_raw_balance_of(vault.safe_address),
        "vault_assets": base_usdc.fetch_raw_balance_of(vault.address),
    }
    combined_settle = vault.settle_via_trading_strategy_module(Decimal(5))
    settlement_limit_selector = Web3.keccak(text="LagoonSettlementLimitExceeded(uint256,uint256)")[:4]
    with pytest.raises(ExtraValueError) as exc_info:
        combined_settle.call({"from": asset_manager})
    revert_data = Web3.to_bytes(hexstr=exc_info.value.args[0]["data"])
    assert revert_data[:4] == settlement_limit_selector
    actual_amount, max_amount = decode(["uint256", "uint256"], revert_data[4:])
    assert actual_amount > cap_raw
    assert max_amount == cap_raw

    tx_hash = combined_settle.transact({"from": asset_manager, "gas": 1_000_000})
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 0
    combined_state_after = {
        "pending_deposit": vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number),
        "pending_redemption": vault.get_flow_manager().fetch_pending_redemption(web3.eth.block_number),
        "safe_assets": base_usdc.fetch_raw_balance_of(vault.safe_address),
        "vault_assets": base_usdc.fetch_raw_balance_of(vault.address),
    }
    assert combined_state_after == combined_state_before


def test_lagoon_v05_max_settlement_rejects_atomically_and_safe_can_recover(
    web3: Web3,
    base_usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    new_depositor: HexAddress,
    deployer_hot_wallet: HotWallet,
) -> None:
    """Reject an over-cap module settlement without state changes, then allow Safe recovery."""
    asset_manager = topped_up_asset_manager
    deposit_amount = Decimal(9)
    deposit_raw = base_usdc.convert_to_raw(deposit_amount)
    deployment = _deploy_capped_vault(
        web3,
        deployer_hot_wallet,
        asset_manager,
        web3.eth.accounts[2],
        Decimal(8),
        use_config_api=False,
        settlement_cooldown=3_600,
    )
    vault = deployment.vault
    module = deployment.trading_strategy_module
    lagoon_events = get_deployed_contract(web3, "guard/LagoonLib.json", module.address)
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [
        3_600,
        0,
        0,
    ]

    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    _request_deposit(deployment, base_usdc, new_depositor, deposit_raw)
    tx_hash = vault.post_new_valuation(Decimal(0)).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    state_before = {
        "pending_deposit": vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number),
        "silo_assets": base_usdc.fetch_raw_balance_of(vault.silo_address),
        "safe_assets": base_usdc.fetch_raw_balance_of(vault.safe_address),
        "vault_assets": base_usdc.fetch_raw_balance_of(vault.address),
        "total_assets": vault.vault_contract.functions.totalAssets().call(),
        "total_supply": vault.vault_contract.functions.totalSupply().call(),
    }

    settle_call = vault.settle_via_trading_strategy_module(Decimal(0))
    expected_error = Web3.keccak(text="LagoonSettlementLimitExceeded(uint256,uint256)")[:4] + encode(
        ["uint256", "uint256"],
        [deposit_raw, base_usdc.convert_to_raw(Decimal(8))],
    )
    with pytest.raises(ExtraValueError) as exc_info:
        settle_call.call({"from": asset_manager})
    assert exc_info.value.args[0]["data"] == Web3.to_hex(expected_error)

    tx_hash = settle_call.transact({"from": asset_manager, "gas": 1_000_000})
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    settlement_events = lagoon_events.events.LagoonSettlementValidated().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
    assert receipt["status"] == 0, [dict(event["args"]) for event in settlement_events]
    assert settlement_events == ()

    state_after = {
        "pending_deposit": vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number),
        "silo_assets": base_usdc.fetch_raw_balance_of(vault.silo_address),
        "safe_assets": base_usdc.fetch_raw_balance_of(vault.safe_address),
        "vault_assets": base_usdc.fetch_raw_balance_of(vault.address),
        "total_assets": vault.vault_contract.functions.totalAssets().call(),
        "total_supply": vault.vault_contract.functions.totalSupply().call(),
    }
    assert state_after == state_before
    assert state_after["pending_deposit"] == deposit_amount
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [
        3_600,
        0,
        0,
    ]

    direct_settlement = vault.vault_contract.functions.settleDeposit(0)
    safe_tx = vault.safe.build_multisig_tx(
        vault.address,
        0,
        bytes.fromhex(direct_settlement._encode_transaction_data().removeprefix("0x")),
    )
    private_key = deployer_hot_wallet.account._private_key.hex()
    safe_tx.sign(private_key)
    tx_hash, _ = execute_safe_tx(
        safe_tx,
        tx_sender_private_key=private_key,
        tx_gas=1_500_000,
        hot_wallet=deployer_hot_wallet,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert vault.get_flow_manager().fetch_pending_deposit(web3.eth.block_number) == 0
    assert base_usdc.fetch_raw_balance_of(vault.safe_address) == deposit_raw
    assert module.functions.getLagoonSettlementCooldownConfig(vault.address).call() == [
        3_600,
        0,
        0,
    ]

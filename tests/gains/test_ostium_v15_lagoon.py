"""Lagoon vault + Ostium V1.5 guard integration test.

Tests the full async deposit/withdraw cycle through a Lagoon vault's
TradingStrategyModuleV0 guard, verifying that the new Ostium V1.5
function selectors are properly whitelisted and executable.

Uses a post-V1.5 Arbitrum fork.
"""

import logging
import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import (
    OstiumDepositRequest,
    OstiumDepositTicket,
    OstiumRedemptionRequest,
    OstiumRedemptionTicket,
    OstiumV15DepositManager,
    OSTIUM_REQUEST_STATUS_CLAIMABLE,
    OSTIUM_REQUEST_STATUS_PENDING,
)
from eth_defi.erc_4626.vault_protocol.gains.testing import force_ostium_v15_settlement
from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault, OstiumVersion
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonDeploymentParameters,
    deploy_automated_lagoon_vault,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDC_NATIVE_TOKEN, USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus


JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
CI = os.environ.get("CI") == "true"
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")

#: Post-upgrade fork block (V1.5 was deployed at block 457,238,658)
FORK_BLOCK = 470_000_000


@pytest.fixture(scope="module")
def asset_manager() -> HexAddress:
    """The asset manager role."""
    return "0x0b2582E9Bf6AcE4E7f42883d4E91240551cf0947"


@pytest.fixture()
def anvil_arbitrum_fork(request, asset_manager) -> AnvilLaunch:
    """Fresh Arbitrum fork for each test with unlocked USDC whale and asset manager."""
    usdc_whale = USDC_WHALE[42161]
    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=FORK_BLOCK,
        unlocked_addresses=[usdc_whale, asset_manager],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_arbitrum_fork):
    return create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url, retries=1)


@pytest.fixture()
def topped_up_asset_manager(web3, asset_manager) -> HexAddress:
    """Asset manager topped up with ETH for gas."""
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
    return fetch_erc20_details(web3, USDC_NATIVE_TOKEN[42161])


@pytest.fixture()
def ostium_vault(web3) -> OstiumVault:
    """Ostium LP vault on Arbitrum at post-V1.5 block."""
    vault = create_vault_instance_autodetect(web3, "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")
    assert isinstance(vault, OstiumVault)
    assert vault.version == OstiumVersion.v1_5
    return vault


@pytest.fixture()
def deployer_hot_wallet(web3) -> HotWallet:
    """Manual nonce manager used for Lagoon deployment."""
    return HotWallet.create_for_testing(web3, eth_amount=1)


@pytest.fixture()
def multisig_owners(web3) -> list[HexAddress]:
    """Accounts set as Safe multisig owners."""
    return [web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]]


@pytest.fixture()
def new_depositor(web3, usdc) -> HexAddress:
    """User with 500 USDC ready to deposit into the Lagoon vault."""
    depositor = web3.eth.accounts[5]
    tx_hash = usdc.transfer(depositor, Decimal(500)).transact({"from": USDC_WHALE[42161], "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return depositor


@pytest.mark.skipif(CI, reason="Skipped on CI due to RPC inconsistencies")
def test_lagoon_ostium_v15_deposit_withdraw(
    web3: Web3,
    usdc: TokenDetails,
    topped_up_asset_manager: HexAddress,
    ostium_vault: OstiumVault,
    deployer_hot_wallet: HotWallet,
    multisig_owners: list[HexAddress],
    new_depositor: HexAddress,
    asset_manager: HexAddress,
):
    """Full async deposit and withdrawal through Lagoon guard to Ostium V1.5 vault.

    1. Deploy Lagoon vault with Ostium V1.5 whitelisted
    2. Fund the Lagoon vault via depositor
    3. Approve USDC to Ostium vault via guard
    4. Call requestDeposit via guard
    5. Force settlement
    6. Call claimDeposit via guard
    7. Verify OLP shares in safe
    8. Call requestWithdraw via guard
    9. Force settlement(s) for withdrawal
    10. Call claimWithdraw via guard
    11. Verify USDC returned to safe
    """
    asset_manager = topped_up_asset_manager
    depositor = new_depositor
    target_vault = ostium_vault

    # 1. Deploy Lagoon vault with Ostium whitelisted
    chain_id = web3.eth.chain_id
    parameters = LagoonDeploymentParameters(
        underlying=USDC_NATIVE_TOKEN[chain_id],
        name="TestOstium",
        symbol="TOST",
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

    vault = deploy_info.vault
    our_address = vault.safe_address

    # Verify Ostium vault was whitelisted
    module = vault.trading_strategy_module
    assert module.functions.isAllowedApprovalDestination(target_vault.vault_address).call()

    # 2. Fund the Lagoon vault
    bound_func = vault.post_new_valuation(Decimal(0))
    tx_hash = bound_func.transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    usdc_deposit = 100 * 10**6
    tx_hash = usdc.contract.functions.approve(vault.address, usdc_deposit).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = vault.request_deposit(depositor, usdc_deposit).transact({"from": depositor})
    assert_transaction_success_with_explanation(web3, tx_hash)

    valuation = Decimal(0)
    tx_hash = vault.post_new_valuation(valuation).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = vault.settle_via_trading_strategy_module(valuation).transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    safe_usdc = usdc.fetch_balance_of(our_address)
    assert safe_usdc > 0, f"Safe should have USDC after funding, got {safe_usdc}"

    # 3. Approve USDC to Ostium vault and requestDeposit via guard
    deposit_manager = target_vault.get_deposit_manager()
    assert isinstance(deposit_manager, OstiumV15DepositManager)

    deposit_amount = Decimal(50)
    deposit_request = deposit_manager.create_deposit_request(our_address, amount=deposit_amount)
    assert isinstance(deposit_request, OstiumDepositRequest)

    fn_calls = [
        usdc.approve(target_vault.vault_address, deposit_amount),
        deposit_request.funcs[0],
    ]
    tx_hashes = []
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)
        tx_hashes.append(tx_hash)

    # 4. Parse the deposit request event
    deposit_ticket = deposit_request.parse_deposit_transaction(tx_hashes)
    assert isinstance(deposit_ticket, OstiumDepositTicket)
    assert deposit_ticket.settlement_id > 0

    # Verify PENDING status
    status = deposit_manager.get_deposit_request_status(deposit_ticket)
    assert status == AsyncVaultRequestStatus.pending

    # 5. Force settlement
    force_ostium_v15_settlement(target_vault, asset_manager)

    # 6. Verify CLAIMABLE and claim via guard
    status = deposit_manager.get_deposit_request_status(deposit_ticket)
    assert status == AsyncVaultRequestStatus.claimable

    claim_func = deposit_manager.finish_deposit(deposit_ticket)
    moduled_tx = vault.transact_via_trading_strategy_module(claim_func)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash, func=claim_func)

    # 7. Verify OLP shares received by safe
    share_token = target_vault.share_token
    shares = share_token.fetch_balance_of(our_address)
    assert shares > 0, f"Safe should have OLP shares after claim, got {shares}"

    # Verify analyse_deposit works
    analysis = deposit_manager.analyse_deposit(tx_hash, deposit_ticket)
    assert analysis.share_count > 0
    assert analysis.denomination_amount > 0

    # 8. Request withdrawal via guard
    redemption_request = deposit_manager.create_redemption_request(
        owner=our_address,
        shares=shares,
    )
    assert isinstance(redemption_request, OstiumRedemptionRequest)

    fn_calls = [redemption_request.funcs[0]]
    tx_hashes = []
    for fn_call in fn_calls:
        moduled_tx = vault.transact_via_trading_strategy_module(fn_call)
        tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash, func=fn_call)
        tx_hashes.append(tx_hash)

    redemption_ticket = redemption_request.parse_redeem_transaction(tx_hashes)
    assert isinstance(redemption_ticket, OstiumRedemptionTicket)
    assert redemption_ticket.settlement_id > 0

    # Verify PENDING status
    status = deposit_manager.get_redemption_request_status(redemption_ticket)
    assert status == AsyncVaultRequestStatus.pending

    # 9. Force settlement(s) for withdrawal
    withdraw_target = target_vault.vault_contract.functions.targetSettlementId(False).call()
    last_id = target_vault.vault_contract.functions.lastSettlementId().call()
    settlements_needed = max(withdraw_target - last_id, 1)
    for _ in range(settlements_needed):
        force_ostium_v15_settlement(target_vault, asset_manager)

    # 10. Verify CLAIMABLE and claim withdrawal via guard
    status = deposit_manager.get_redemption_request_status(redemption_ticket)
    assert status == AsyncVaultRequestStatus.claimable

    claim_func = deposit_manager.finish_redemption(redemption_ticket)
    moduled_tx = vault.transact_via_trading_strategy_module(claim_func)
    tx_hash = moduled_tx.transact({"from": asset_manager, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash, func=claim_func)

    # Verify analyse_redemption works
    analysis = deposit_manager.analyse_redemption(tx_hash, redemption_ticket)
    assert analysis.denomination_amount > 0

    # 11. Verify USDC returned and shares gone
    remaining_shares = share_token.fetch_balance_of(our_address)
    assert remaining_shares == 0

    final_usdc = usdc.fetch_balance_of(our_address)
    assert final_usdc > safe_usdc - deposit_amount, "Should get back approximately what was deposited"

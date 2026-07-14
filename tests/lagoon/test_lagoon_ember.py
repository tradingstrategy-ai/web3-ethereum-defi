"""Exercise Ember's no-claim redemption flow through a Lagoon Safe."""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.ember.deposit_redeem import EmberDepositManager, EmberRedemptionTicket
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonDeploymentParameters, deploy_automated_lagoon_vault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, USDC_WHALE, TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
EMBER_VAULT = HexAddress(HexStr("0xf3190A3ECC109F88e7947b849b281918c798A0C4"))
EMBER_OPERATOR = HexAddress(HexStr("0x116046991e3F0B0967723073a87820eF5edB29f2"))
FORK_BLOCK = 24_496_689

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_ember_lagoon_fork() -> AnvilLaunch:
    """Fork the Ember version used by the integration lifecycle."""
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        fork_block_number=FORK_BLOCK,
        unlocked_addresses=[USDC_WHALE[1], EMBER_OPERATOR],
    )
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_ember_lagoon_fork: AnvilLaunch) -> Web3:
    """Connect to the isolated Ethereum fork."""
    return create_multi_provider_web3(anvil_ethereum_ember_lagoon_fork.json_rpc_url, retries=2)


@pytest.fixture(scope="module")
def ember_vault(web3: Web3) -> EmberVault:
    """Open the Ember target vault."""
    vault = create_vault_instance_autodetect(web3, EMBER_VAULT)
    assert isinstance(vault, EmberVault)
    return vault


@pytest.fixture(scope="module")
def usdc(web3: Web3) -> TokenDetails:
    """Open Ethereum USDC."""
    return fetch_erc20_details(web3, USDC_NATIVE_TOKEN[1])


def test_lagoon_safe_ember_redemption_cycle(web3: Web3, ember_vault: EmberVault, usdc: TokenDetails) -> None:
    """Lagoon Safe deposits, queues Ember redemption and receives operator payout."""
    asset_manager = web3.eth.accounts[1]
    depositor = web3.eth.accounts[5]
    fund_hash = usdc.transfer(depositor, Decimal("500")).transact({"from": USDC_WHALE[1]})
    assert_transaction_success_with_explanation(web3, fund_hash)

    deployment = deploy_automated_lagoon_vault(
        web3=web3,
        deployer=HotWallet.create_for_testing(web3, eth_amount=1),
        asset_manager=asset_manager,
        parameters=LagoonDeploymentParameters(underlying=USDC_NATIVE_TOKEN[1], name="TestEmber", symbol="TEMB"),
        safe_owners=[web3.eth.accounts[2], web3.eth.accounts[3], web3.eth.accounts[4]],
        safe_threshold=2,
        uniswap_v2=None,
        uniswap_v3=None,
        any_asset=False,
        erc_4626_vaults=[ember_vault],
        from_the_scratch=True,
        use_forge=True,
    )
    lagoon_vault = deployment.vault
    safe_address = lagoon_vault.safe_address
    assert lagoon_vault.trading_strategy_module.functions.isAllowedApprovalDestination(ember_vault.address).call()

    for func in [
        lagoon_vault.post_new_valuation(Decimal(0)),
        usdc.contract.functions.approve(lagoon_vault.address, usdc.convert_to_raw(Decimal("100"))),
        lagoon_vault.request_deposit(depositor, usdc.convert_to_raw(Decimal("100"))),
        lagoon_vault.post_new_valuation(Decimal(0)),
        lagoon_vault.settle_via_trading_strategy_module(Decimal(0)),
    ]:
        sender = depositor if func.fn_name in {"approve", "requestDeposit"} else asset_manager
        tx_hash = func.transact({"from": sender, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)
    assert usdc.fetch_balance_of(safe_address) > 0

    manager = ember_vault.get_deposit_manager()
    assert isinstance(manager, EmberDepositManager)
    deposit_request = manager.create_deposit_request(owner=safe_address, amount=Decimal("100"))
    for func in [usdc.approve(ember_vault.address, Decimal("100")), *deposit_request.funcs]:
        tx_hash = lagoon_vault.transact_via_trading_strategy_module(func).transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_analysis = manager.analyse_deposit(tx_hash, deposit_request.parse_deposit_transaction([tx_hash]))
    assert deposit_analysis.share_count == Decimal("97.218907")

    raw_shares = ember_vault.share_token.fetch_raw_balance_of(safe_address)
    assert raw_shares == 97_218_907
    redemption_request = manager.create_redemption_request(owner=safe_address, raw_shares=raw_shares)
    request_hashes = []
    for func in redemption_request.funcs:
        tx_hash = lagoon_vault.transact_via_trading_strategy_module(func).transact({"from": asset_manager, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)
        request_hashes.append(tx_hash)
    ticket = redemption_request.parse_redeem_transaction(request_hashes)
    assert isinstance(ticket, EmberRedemptionTicket)
    assert ticket.request_sequence_number == 145
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.pending
    assert manager.can_finish_redeem(ticket) is False
    assert manager.finish_redemption(ticket) is None

    operator_hash = ember_vault.vault_contract.functions.processWithdrawalRequests(1).transact({"from": EMBER_OPERATOR})
    assert_transaction_success_with_explanation(web3, operator_hash)
    assert manager.fetch_completed_redemption_tx_hash(ticket) == operator_hash
    analysis = manager.analyse_redemption(operator_hash, ticket)
    assert analysis.denomination_amount == Decimal("99.999999")
    assert ember_vault.share_token.fetch_raw_balance_of(safe_address) == 0
    assert usdc.fetch_raw_balance_of(safe_address) == usdc.convert_to_raw(Decimal("99.999999"))

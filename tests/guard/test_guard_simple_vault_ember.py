"""Exercise Ember's complete operator-finalised withdrawal cycle through GuardV0."""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.ember.deposit_redeem import EmberDepositManager, EmberRedemptionTicket
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import USDC_WHALE, TokenDetails, fetch_erc20_details
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
EMBER_VAULT = HexAddress(HexStr("0xf3190A3ECC109F88e7947b849b281918c798A0C4"))
EMBER_OPERATOR = HexAddress(HexStr("0x116046991e3F0B0967723073a87820eF5edB29f2"))
FORK_BLOCK = 24_496_689

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_ember_guard_fork() -> AnvilLaunch:
    """Fork Ember v1.1.1 with the USDC whale and Ember operator unlocked."""
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
def web3(anvil_ethereum_ember_guard_fork: AnvilLaunch) -> Web3:
    """Connect to the fixed Ethereum fork."""
    return create_multi_provider_web3(anvil_ethereum_ember_guard_fork.json_rpc_url, retries=2)


@pytest.fixture(scope="module")
def ember_vault(web3: Web3) -> EmberVault:
    """Open the fixed Ember vault."""
    vault = create_vault_instance_autodetect(web3, EMBER_VAULT)
    assert isinstance(vault, EmberVault)
    return vault


@pytest.fixture(scope="module")
def usdc(web3: Web3) -> TokenDetails:
    """Open Ethereum native USDC."""
    return fetch_erc20_details(web3, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")


def test_guarded_ember_deposit_and_redemption_cycle(web3: Web3, ember_vault: EmberVault, usdc: TokenDetails) -> None:
    """Guard approves, deposits, queues redemption and accepts only Safe payout."""
    deployer = web3.eth.accounts[0]
    asset_manager = web3.eth.accounts[1]
    owner = web3.eth.accounts[2]
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = guard.functions.whitelistERC4626(ember_vault.address, "Allow Ember").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, whitelist_hash)

    amount = Decimal("100")
    raw_amount = usdc.convert_to_raw(amount)
    fund_hash = usdc.transfer(simple_vault.address, amount).transact({"from": USDC_WHALE[1]})
    assert_transaction_success_with_explanation(web3, fund_hash)
    manager = ember_vault.get_deposit_manager()
    assert isinstance(manager, EmberDepositManager)

    deposit_request = manager.create_deposit_request(owner=simple_vault.address, amount=amount)
    for func in [usdc.approve(manager.get_deposit_approval_target(), amount), *deposit_request.funcs]:
        target, call_data = encode_simple_vault_transaction(func)
        tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_analysis = manager.analyse_deposit(tx_hash, deposit_request.parse_deposit_transaction([tx_hash]))
    assert deposit_analysis.denomination_amount == amount
    assert deposit_analysis.share_count == Decimal("97.218907")
    raw_shares = ember_vault.share_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares == 97_218_907

    malicious_request = manager.create_redemption_request(
        owner=simple_vault.address,
        to=web3.eth.accounts[4],
        raw_shares=raw_shares,
    )
    malicious_target, malicious_data = encode_simple_vault_transaction(malicious_request.funcs[1])
    malicious_hash = simple_vault.functions.performCall(malicious_target, malicious_data).transact({"from": asset_manager})
    with pytest.raises(TransactionAssertionError, match="Receiver not whitelisted"):
        assert_transaction_success_with_explanation(web3, malicious_hash)

    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    approval_target, approval_data = encode_simple_vault_transaction(redemption_request.funcs[0])
    approval_hash = simple_vault.functions.performCall(approval_target, approval_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, approval_hash)
    request_target, request_data = encode_simple_vault_transaction(redemption_request.funcs[1])
    request_hash = simple_vault.functions.performCall(request_target, request_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, request_hash)
    ticket = redemption_request.parse_redeem_transaction([approval_hash, request_hash])
    assert isinstance(ticket, EmberRedemptionTicket)
    assert ticket.request_sequence_number == 145
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.pending
    assert ember_vault.vault_contract.functions.getAccountState(simple_vault.address).call() == [raw_shares, [145], []]
    assert manager.can_finish_redeem(ticket) is False
    assert manager.finish_redemption(ticket) is None

    operator_hash = ember_vault.vault_contract.functions.processWithdrawalRequests(1).transact({"from": EMBER_OPERATOR})
    assert_transaction_success_with_explanation(web3, operator_hash)
    assert manager.fetch_completed_redemption_tx_hash(ticket) == operator_hash
    analysis = manager.analyse_redemption(operator_hash, ticket)
    assert analysis.share_count == Decimal("97.218907")
    assert analysis.denomination_amount == Decimal("99.999999")
    assert ember_vault.share_token.fetch_raw_balance_of(simple_vault.address) == 0
    assert usdc.fetch_raw_balance_of(simple_vault.address) == raw_amount - 1

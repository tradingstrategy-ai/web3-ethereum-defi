"""Exercise Ember deposit and operator-finalised redemption on an Ethereum fork."""

import datetime
import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress, HexStr
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.ember.deposit_redeem import EmberDepositManager, EmberRedemptionTicket
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_WHALE, TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
EMBER_VAULT = HexAddress(HexStr("0xf3190A3ECC109F88e7947b849b281918c798A0C4"))
EMBER_OPERATOR = HexAddress(HexStr("0x116046991e3F0B0967723073a87820eF5edB29f2"))
FORK_BLOCK = 24_496_689

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_ember_fork() -> AnvilLaunch:
    """Fork Ember v1.1.1 with its operator and an Ethereum USDC whale unlocked."""
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
def web3(anvil_ethereum_ember_fork: AnvilLaunch) -> Web3:
    """Connect to the reproducible Ember Anvil fork."""
    return create_multi_provider_web3(anvil_ethereum_ember_fork.json_rpc_url, retries=2)


@pytest.fixture(scope="module")
def vault(web3: Web3) -> EmberVault:
    """Open the Crosschain USD Ember vault through protocol autodetection."""
    vault = create_vault_instance_autodetect(web3, EMBER_VAULT)
    assert isinstance(vault, EmberVault)
    return vault


@pytest.fixture(scope="module")
def usdc(web3: Web3) -> TokenDetails:
    """Open Ethereum native USDC on the fork."""
    return fetch_erc20_details(web3, "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")


def test_ember_deposit_redeem_lifecycle(web3: Web3, vault: EmberVault, usdc: TokenDetails) -> None:
    """Deposit, request redemption, operator-process and analyse exact Ember amounts."""
    # 1. Pin the deployed ABI and public mixed-flow capability.
    manager = vault.get_deposit_manager()
    assert isinstance(manager, EmberDepositManager)
    assert vault.vault_contract.functions.version().call() == "v1.1.1"
    assert [input_["name"] for input_ in vault.vault_contract.events.RequestProcessed().abi["inputs"]] == [
        "vault",
        "owner",
        "receiver",
        "shares",
        "withdrawAmount",
        "requestTimestamp",
        "processTimestamp",
        "skipped",
        "cancelled",
        "totalShares",
        "totalSharesPendingToBurn",
        "sequenceNumber",
        "requestSequenceNumber",
    ]
    assert get_topic_signature_from_event(vault.vault_contract.events.RequestRedeemed) == "0xa860c7ba918bd53ab101f8fa1e1e8cee055aedf31b1d9c5b12401a91d79b17bd"
    assert get_topic_signature_from_event(vault.vault_contract.events.RequestProcessed) == "0x14239ade46d853ae1a98641c2a237d05a11e24ff2678eb6bf0e409953779a057"
    assert manager.has_synchronous_deposit() is True
    assert manager.has_synchronous_redemption() is False
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "asynchronous",
    }

    owner = web3.eth.accounts[0]
    amount = Decimal("100")
    # 2. Fund the depositor and complete the synchronous Ember deposit.
    transfer_hash = usdc.transfer(owner, amount).transact({"from": USDC_WHALE[1]})
    assert_transaction_success_with_explanation(web3, transfer_hash)
    approve_hash = usdc.approve(vault.address, amount).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approve_hash)

    assert manager.estimate_deposit(owner, amount, FORK_BLOCK) == Decimal("97.218907")
    deposit_request = manager.create_deposit_request(owner=owner, amount=amount)
    deposit_ticket = deposit_request.broadcast(from_=owner)
    deposit_analysis = manager.analyse_deposit(deposit_ticket.tx_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == amount
    assert deposit_analysis.share_count == Decimal("97.218907")
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares == 97_218_907
    assert manager.estimate_redeem(owner, Decimal("97.218907"), "latest") > Decimal("99")

    # 3. Queue the redemption and persist its request identity.
    redemption_request = manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    assert len(redemption_request.funcs) == 2
    ticket = redemption_request.broadcast(from_=owner)
    assert isinstance(ticket, EmberRedemptionTicket)
    assert ticket.request_sequence_number == 145
    assert ticket.raw_shares == raw_shares
    assert ticket.block_timestamp.tzinfo is None
    assert manager.reconstruct_redemption_ticket(manager.serialize_redemption_ticket(ticket)) == ticket
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.pending
    assert manager.is_redemption_in_progress(owner) is True
    account_state = vault.vault_contract.functions.getAccountState(owner).call()
    assert account_state == [raw_shares, [145], []]
    assert manager.fetch_completed_redemption_tx_hash(ticket) is None
    assert manager.can_finish_redeem(ticket) is False
    assert manager.finish_redemption(ticket) is None

    # 4. Let only the external operator process and pay the request.
    process_hash = vault.vault_contract.functions.processWithdrawalRequests(1).transact({"from": EMBER_OPERATOR})
    assert_transaction_success_with_explanation(web3, process_hash)
    completion_hash = manager.fetch_completed_redemption_tx_hash(ticket)
    assert completion_hash == process_hash
    redemption_analysis = manager.analyse_redemption(completion_hash, ticket)
    assert redemption_analysis.share_count == Decimal("97.218907")
    assert redemption_analysis.denomination_amount == Decimal("99.999999")
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.none
    assert vault.vault_contract.functions.getAccountState(owner).call() == [0, [], []]
    assert vault.share_token.fetch_raw_balance_of(owner) == 0
    assert usdc.fetch_raw_balance_of(owner) == 99_999_999


def test_ember_redemption_minimum_is_checked_before_call_binding(web3: Web3, vault: EmberVault) -> None:
    """Reject a request below the exact configured Ember minimum share amount."""
    manager = vault.get_deposit_manager()
    with pytest.raises(ValueError, match="below minimum"):
        manager.create_redemption_request(
            owner=web3.eth.accounts[1],
            raw_shares=99_999,
            check_enough_token=False,
        )


def test_ember_ticket_identity_validation() -> None:
    """Reject a terminal event whose owner, receiver or shares disagree with a ticket."""
    ticket = EmberRedemptionTicket(
        vault_address=EMBER_VAULT,
        owner=HexAddress(HexStr("0x74588dD3661781bfa0B497C613ad861B3Dae6F32")),
        to=HexAddress(HexStr("0x74588dD3661781bfa0B497C613ad861B3Dae6F32")),
        raw_shares=30_000_000,
        tx_hash=HexBytes("0x18165ec393dbba57b6bd1802925abce160ee15d78caf389725bbd7c73ea14dca"),
        request_sequence_number=29,
        block_number=24_286_355,
        block_timestamp=datetime.datetime(2026, 1, 21, 23, 9, 23),
    )
    with pytest.raises(ValueError, match="receiver"):
        EmberDepositManager._validate_processed_event(
            ticket,
            {
                "requestSequenceNumber": 29,
                "owner": ticket.owner,
                "receiver": "0x0000000000000000000000000000000000000001",
                "shares": 30_000_000,
            },
        )

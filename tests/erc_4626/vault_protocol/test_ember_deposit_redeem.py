"""Exercise Ember deposit and operator-finalised redemption on an Ethereum fork."""

import datetime
import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_typing import HexAddress, HexStr
from hexbytes import HexBytes
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.ember.deposit_redeem import EmberDepositManager, EmberRedemptionTicket
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.token import USDC_WHALE, TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, UnsupportedVaultSimulation, VaultFlowUnavailable

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
EMBER_VAULT = HexAddress(HexStr("0xf3190A3ECC109F88e7947b849b281918c798A0C4"))
EMBER_OPERATOR = HexAddress(HexStr("0x116046991e3F0B0967723073a87820eF5edB29f2"))
FORK_BLOCK = 24_496_689
EMBER_EXACT_VAULTS = (
    HexAddress(HexStr("0x9be9294722f8aad37b11a9792be2c782182cafa2")),
    HexAddress(HexStr("0x0b9342c15143e8f54a83f887c280a922f4c48771")),
    EMBER_VAULT,
    HexAddress(HexStr("0x373152feef81cc59502da2c8de877b3d5ae2e342")),
)

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:24496689"),
]


@pytest.fixture(scope="module")
def anvil_ethereum_ember_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Reuse the warmed Ethereum fork for all Ember deployment trials."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_ETHEREUM,
        FORK_BLOCK,
        unlocked_addresses=[USDC_WHALE[1]],
    )


@pytest.fixture(scope="module")
def web3(anvil_ethereum_ember_fork: AnvilLaunch) -> Web3:
    """Connect to the reproducible Ember Anvil fork."""
    return create_multi_provider_web3(anvil_ethereum_ember_fork.json_rpc_url, retries=2)


@pytest.fixture
def ember_snapshot(anvil_ethereum_ember_fork: AnvilLaunch) -> Iterator[None]:
    """Restore pooled fork state after each mutating Ember lifecycle trial."""
    yield from evm_snapshot_revert(anvil_ethereum_ember_fork)


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


def test_ember_deposit_redeem_lifecycle(web3: Web3, vault: EmberVault, usdc: TokenDetails, ember_snapshot: None) -> None:
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
        "supports_anvil_settlement": True,
    }
    # Keep the exact deployment's operator layout in coverage even though its
    # direct-pay queue cannot satisfy generic claimable-ticket settlement.
    assert Web3.to_checksum_address(vault.vault_contract.functions.roles().call()[1]) == Web3.to_checksum_address(EMBER_OPERATOR)

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

    # 3. Queue two requests. The target request is deliberately second, proving
    # that forced settlement locates it in Ember's vault-global FIFO queue.
    first_raw_shares = 10_000_000
    first_request = manager.create_redemption_request(owner=owner, raw_shares=first_raw_shares)
    assert len(first_request.funcs) == 2
    first_ticket = first_request.broadcast(from_=owner)
    redemption_request = manager.create_redemption_request(owner=owner, raw_shares=raw_shares - first_raw_shares)
    assert len(redemption_request.funcs) == 2
    ticket = redemption_request.broadcast(from_=owner)
    assert isinstance(first_ticket, EmberRedemptionTicket)
    assert first_ticket.request_sequence_number == 145
    assert isinstance(ticket, EmberRedemptionTicket)
    assert ticket.request_sequence_number == 146
    assert ticket.raw_shares == raw_shares - first_raw_shares
    assert ticket.block_timestamp.tzinfo is None
    assert manager.reconstruct_redemption_ticket(manager.serialize_redemption_ticket(ticket)) == ticket
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.pending
    assert manager.is_redemption_in_progress(owner) is True
    account_state = vault.vault_contract.functions.getAccountState(owner).call()
    assert account_state == [raw_shares, [145, 146], []]
    assert manager.fetch_completed_redemption_tx_hash(ticket) is None
    assert manager.fetch_pending_withdrawal_index(ticket) == 1
    assert manager.can_finish_redeem(ticket) is False
    assert manager.finish_redemption(ticket) is None

    # 4. Ember's configured operator pays directly rather than marking the
    # request claimable. The result must carry the matching event and a
    # positive USDC balance delta before it is terminal success.
    # Set up a deterministic queue-liquidity shortfall: strict fork simulation
    # must refuse it, while the default Anvil-only driver transparently tops up
    # both verified settlement sources to prove the operator-processing mechanism.
    fund_erc20_on_anvil(web3, usdc.address, vault.address, 0)
    operator = vault.vault_contract.functions.roles().call()[1]
    pending_raw_amount = sum(int(vault.vault_contract.functions.getPendingWithdrawal(index).call()[3]) for index in range(manager.fetch_pending_withdrawal_index(ticket) + 1))
    expected_synthetic_raw = sum(max(pending_raw_amount - usdc.fetch_raw_balance_of(address), 0) for address in (vault.address, operator))
    with pytest.raises(UnsupportedVaultSimulation) as exc_info:
        manager.force_settle(ticket, ignore_liquidity=False)
    assert exc_info.value.unsupported_reason == "ember_settlement_insufficient_liquidity"

    usdc_before = usdc.fetch_raw_balance_of(owner)
    settlement = manager.force_settle(ticket)
    assert settlement.status_before is AsyncVaultRequestStatus.pending
    assert settlement.status_after is AsyncVaultRequestStatus.none
    assert settlement.direct_payout_evidence is not None
    assert settlement.direct_payout_evidence.request_id == ticket.request_sequence_number
    assert settlement.direct_payout_evidence.receiver == owner
    assert settlement.direct_payout_evidence.event_name == "RequestProcessed"
    assert settlement.direct_payout_evidence.raw_balance_after > settlement.direct_payout_evidence.raw_balance_before
    assert settlement.synthetic_assets_injected_raw == expected_synthetic_raw
    assert settlement.liquidity_constraints_ignored is True
    assert settlement.is_terminal_success() is True
    assert usdc.fetch_raw_balance_of(owner) > usdc_before
    assert manager.get_redemption_request_status(ticket) is AsyncVaultRequestStatus.none
    assert manager.fetch_completed_redemption_tx_hash(ticket) == settlement.transaction_hashes[0]


def test_ember_redemption_minimum_is_checked_before_call_binding(web3: Web3, vault: EmberVault, ember_snapshot: None) -> None:
    """Expose and enforce Ember's configured minimum redemption shares.

    1. Read the raw and decimal values through the shared vault API.
    2. Submit a request one raw unit below the source-proven minimum.
    3. Verify the manager reports the same exact minimum before binding calls.
    """
    # 1. Read the raw and decimal values through the shared vault API.
    manager = vault.get_deposit_manager()
    assert vault.fetch_minimum_raw_redemption() == 100_000
    assert vault.fetch_minimum_redemption() == Decimal("0.1")

    # 2. Submit a request one raw unit below the source-proven minimum.
    with pytest.raises(VaultFlowUnavailable, match="below minimum") as exc_info:
        manager.create_redemption_request(
            owner=web3.eth.accounts[1],
            raw_shares=99_999,
            check_enough_token=False,
        )

    # 3. Verify the manager reports the same exact minimum before binding calls.
    assert exc_info.value.decoded_error == "InsufficientAmount"
    assert exc_info.value.preflight_result == "below_minimum"
    assert exc_info.value.direction == "redeem"
    assert exc_info.value.minimum_raw_amount == 100_000


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


@pytest.mark.parametrize("vault_address", EMBER_EXACT_VAULTS)
def test_all_exact_ember_vaults_expose_a_settlement_operator(web3: Web3, vault_address: HexAddress) -> None:
    """Characterise each matrix deployment on the same fixed, warmed fork."""
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, EmberVault)
    _admin, operator, _rate_manager = vault.vault_contract.functions.roles().call()
    assert Web3.to_checksum_address(operator) != "0x0000000000000000000000000000000000000000"
    assert vault.get_deposit_manager_capability().supports_anvil_settlement is True

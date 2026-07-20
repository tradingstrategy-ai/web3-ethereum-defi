"""NaraUSD+ hardcoded vault and cooldown lifecycle tests."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR, get_abi_by_filename
from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.nara.constants import NARAUSD_PLUS_VAULT
from eth_defi.erc_4626.vault_protocol.nara.deposit_redeem import NaraDepositManager, NaraRedemptionTicket
from eth_defi.erc_4626.vault_protocol.nara.vault import NaraVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil, mine, set_balance, unlock_account
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
NARAUSD_USDC_CURVE_POOL = "0xf05F1b7bC5D9f966193201e9f4F320A98aAF260C"
DEPOSIT_AMOUNT = Decimal("10")
FORK_BLOCK_NUMBER = 25_575_245


def test_narausd_plus_hardcoded_protocol() -> None:
    """Classify Nara's sole reviewed Ethereum vault by address."""
    features = HARDCODED_PROTOCOLS[NARAUSD_PLUS_VAULT]

    assert features == {ERC4626Feature.nara_like}
    assert get_vault_protocol_name(features) == "Nara"


def test_narausd_plus_abi() -> None:
    """Package NaraUSD+'s complete custom cooldown interface as JSON."""
    abi = get_abi_by_filename("nara/NaraUSDPlus.json")

    assert {entry["name"] for entry in abi} == {
        "cooldownDuration",
        "cooldownShares",
        "cooldowns",
        "unstake",
    }


@pytest.fixture(scope="module")
def anvil_ethereum_fork() -> AnvilLaunch:
    """Fork Ethereum at the NaraUSD+ validation block."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=FORK_BLOCK_NUMBER)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork: AnvilLaunch) -> Web3:
    """Create a Web3 client for the NaraUSD+ fork."""
    return create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)


@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
@flaky.flaky
def test_narausd_plus_vault(web3: Web3) -> None:
    """Read NaraUSD+ metadata and its supported request lifecycle."""
    vault = create_vault_instance_autodetect(web3, vault_address=NARAUSD_PLUS_VAULT)

    assert isinstance(vault, NaraVault)
    assert vault.get_protocol_name() == "Nara"
    assert vault.features == {ERC4626Feature.nara_like}
    assert vault.name == "NaraUSD+"
    assert vault.denomination_token.symbol == "NaraUSD"
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=7)
    assert vault.can_check_redeem() is False
    assert isinstance(vault.get_deposit_manager(), NaraDepositManager)
    assert vault.get_deposit_manager_capability().as_dict() == {
        "can_deposit": True,
        "can_redeem": True,
        "deposit_flow": "synchronous",
        "redemption_flow": "asynchronous",
    }


@pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run this test")
@pytest.mark.timeout(180)
@flaky.flaky
def test_narausd_plus_deposit_and_cooldown_redemption(web3: Web3) -> None:
    """Deposit NaraUSD, cool down exact shares, and claim NaraUSD on the fork."""
    vault = create_vault_instance_autodetect(web3, vault_address=NARAUSD_PLUS_VAULT)
    assert isinstance(vault, NaraVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, NaraDepositManager)

    owner = web3.eth.accounts[0]
    narausd = vault.denomination_token

    with pytest.raises(ValueError, match="exactly one"):
        manager.create_redemption_request(owner=owner)
    with pytest.raises(ValueError, match="exactly one"):
        manager.create_redemption_request(owner=owner, shares=Decimal(1), raw_shares=1)
    with pytest.raises(ValueError, match="zero address"):
        manager.create_redemption_request(owner=owner, to=ZERO_ADDRESS_STR, raw_shares=1)
    with pytest.raises(ValueError, match="positive"):
        manager.create_redemption_request(owner=owner, raw_shares=0)
    with pytest.raises(ValueError, match="Insufficient"):
        manager.create_redemption_request(owner=owner, raw_shares=1)

    set_balance(web3, NARAUSD_USDC_CURVE_POOL, 10**18)
    unlock_account(web3, NARAUSD_USDC_CURVE_POOL)
    funding_hash = narausd.transfer(owner, DEPOSIT_AMOUNT).transact({"from": NARAUSD_USDC_CURVE_POOL})
    assert_transaction_success_with_explanation(web3, funding_hash)
    approval_hash = narausd.approve(vault.address, DEPOSIT_AMOUNT).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)

    manager.create_deposit_request(owner=owner, amount=DEPOSIT_AMOUNT).broadcast(from_=owner)
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares > 0

    redemption_ticket = manager.create_redemption_request(owner=owner, raw_shares=raw_shares).broadcast(from_=owner)
    assert isinstance(redemption_ticket, NaraRedemptionTicket)
    assert redemption_ticket.raw_shares == raw_shares
    assert redemption_ticket.raw_assets > 0
    assert manager.fetch_cooldown(owner) == (
        int(redemption_ticket.cooldown_end.replace(tzinfo=datetime.UTC).timestamp()),
        redemption_ticket.raw_assets,
    )
    assert manager.get_redemption_request_status(redemption_ticket) == AsyncVaultRequestStatus.pending
    assert manager.reconstruct_redemption_ticket(manager.serialize_redemption_ticket(redemption_ticket)) == redemption_ticket
    assert vault.share_token.fetch_raw_balance_of(owner) == 0
    with pytest.raises(ValueError, match="active cooldown"):
        manager.create_redemption_request(owner=owner, raw_shares=1, check_enough_token=False)
    with pytest.raises(ValueError, match="not claimable"):
        manager.finish_redemption(redemption_ticket)

    mine(web3, increase_timestamp=datetime.timedelta(days=7, seconds=1).total_seconds())
    assert manager.can_finish_redeem(redemption_ticket) is True
    assert manager.get_redemption_request_status(redemption_ticket) == AsyncVaultRequestStatus.claimable
    claim_hash = manager.finish_redemption(redemption_ticket).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, claim_hash)
    assert narausd.fetch_raw_balance_of(owner) > 0
    assert manager.get_redemption_request_status(redemption_ticket) == AsyncVaultRequestStatus.none

    approval_hash = narausd.approve(vault.address, DEPOSIT_AMOUNT).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)
    manager.create_deposit_request(owner=owner, amount=DEPOSIT_AMOUNT).broadcast(from_=owner)
    replacement_shares = vault.share_token.fetch_raw_balance_of(owner)
    replacement_ticket = manager.create_redemption_request(owner=owner, raw_shares=replacement_shares).broadcast(from_=owner)

    assert replacement_ticket.cooldown_end != redemption_ticket.cooldown_end
    assert manager.get_redemption_request_status(replacement_ticket) == AsyncVaultRequestStatus.pending
    assert manager.get_redemption_request_status(redemption_ticket) == AsyncVaultRequestStatus.none
    with pytest.raises(ValueError, match="not claimable"):
        manager.finish_redemption(redemption_ticket)

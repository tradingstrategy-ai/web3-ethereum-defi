"""Ostium vault V1.5 async deposit/withdraw tests.

Tests the settlement-based async flow introduced in the V1.5 upgrade
(Arbitrum block 457,238,658). Uses a post-upgrade fork block.
"""

import datetime
import logging
import os
from decimal import Decimal

import pytest

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
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
from eth_defi.erc_4626.vault_protocol.gains.vault import (
    OstiumV15HistoricalReader,
    OstiumVault,
    OstiumVersion,
)
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDC_NATIVE_TOKEN, USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation


JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
CI = os.environ.get("CI") == "true"
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")

#: Post-upgrade fork block (V1.5 was deployed at block 457,238,658)
FORK_BLOCK = 470_000_000


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=FORK_BLOCK)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@pytest.fixture()
def anvil_arbitrum_fork_write(request) -> AnvilLaunch:
    """Fresh fork for each write test."""
    usdc_whale = USDC_WHALE[42161]
    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=FORK_BLOCK,
        unlocked_addresses=[usdc_whale],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_write(anvil_arbitrum_fork_write):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork_write.json_rpc_url, retries=1)
    return web3


@pytest.fixture()
def usdc(web3_write) -> TokenDetails:
    web3 = web3_write
    return fetch_erc20_details(web3, USDC_NATIVE_TOKEN[42161])


@pytest.fixture()
def test_user(web3_write, usdc):
    web3 = web3_write
    account = web3.eth.accounts[0]
    tx_hash = usdc.transfer(account, Decimal(10_000)).transact({"from": USDC_WHALE[42161]})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert web3.eth.get_balance(account) > 10**18
    return account


@pytest.fixture(scope="module")
def vault(web3) -> OstiumVault:
    """Ostium LP vault on Arbitrum at post-upgrade block."""
    vault_address = "0x20d419a8e12c45f88fda7c5760bb6923cee27f98"
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, OstiumVault)
    return vault


def test_ostium_v15_version_detection(web3):
    """Verify version detection returns V1.5 at post-upgrade block.

    1. Create vault instance via autodetect
    2. Assert version is V1.5
    3. Assert correct ABI is loaded
    """
    vault_address = "0x20d419a8e12c45f88fda7c5760bb6923cee27f98"
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, OstiumVault)
    assert vault.version == OstiumVersion.v1_5


def test_ostium_v15_features(web3):
    """Verify feature detection still works at post-upgrade block.

    1. Detect vault features
    2. Assert ostium_like feature present
    """
    vault_address = "0x20d419a8e12c45f88fda7c5760bb6923cee27f98"
    features = detect_vault_features(web3, vault_address, verbose=True)
    assert ERC4626Feature.ostium_like in features, f"Got features: {features}"


@pytest.mark.skipif(CI, reason="Skipped on CI due to RPC inconsistencies")
def test_ostium_v15_read_data(web3, vault: OstiumVault):
    """Read vault metadata and historical state at V1.5 block.

    1. Verify basic vault properties
    2. Get historical reader (should be OstiumV15HistoricalReader)
    3. Run multicalls and process results
    4. Assert deposits_open and redemption_open are True
    5. Verify deposit/redemption closed reason methods work
    """
    assert vault.name == "Ostium Liquidity Pool Vault"
    assert vault.version == OstiumVersion.v1_5

    # V1.5 historical reader
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, OstiumV15HistoricalReader)

    block_number = web3.eth.block_number
    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.timezone.utc).replace(tzinfo=None)

    calls = list(reader.construct_multicalls())
    call_results = [c.call_as_result(web3=web3, block_identifier=block_number) for c in calls]
    vault_read = reader.process_result(block_number, timestamp, call_results)

    assert vault_read.block_number == block_number
    assert vault_read.share_price > 0
    assert vault_read.total_assets > 0
    assert vault_read.total_supply > 0

    # V1.5: both always open, max_deposit is None (V1.5 maxDeposit returns max uint)
    assert vault_read.deposits_open is True
    assert vault_read.redemption_open is True
    assert vault_read.max_deposit is None

    # V1.5: deposit/redemption not closed
    assert vault.fetch_deposit_closed_reason() is None
    assert vault.fetch_redemption_closed_reason() is None

    # V1.5 deposit manager
    deposit_manager = vault.get_deposit_manager()
    assert isinstance(deposit_manager, OstiumV15DepositManager)
    assert deposit_manager.has_synchronous_deposit() is False
    assert deposit_manager.has_synchronous_redemption() is False


@pytest.mark.skipif(CI, reason="Skipped on CI due to RPC inconsistencies")
def test_ostium_v15_async_deposit_withdraw(
    web3_write: Web3,
    test_user,
    usdc: TokenDetails,
):
    """Full async deposit -> settlement -> claim -> withdraw -> settlement -> claim cycle.

    1. Approve USDC to vault
    2. Create deposit request via requestDeposit
    3. Broadcast and parse DepositRequestedV2 event for settlement_id
    4. Assert deposit status is PENDING
    5. Assert is_deposit_in_progress is True
    6. Force settlement via tryNewSettlement
    7. Assert deposit status is CLAIMABLE
    8. Claim deposit and broadcast claimDeposit
    9. Run analyse_deposit on claim tx
    10. Verify OLP share balance
    11. Create redemption request via requestWithdraw
    12. Broadcast and parse WithdrawRequestedV2 event
    13. Assert withdrawal status is PENDING
    14. Force settlement(s) for withdrawal
    15. Assert withdrawal status is CLAIMABLE
    16. Claim withdrawal and broadcast claimWithdraw
    17. Run analyse_redemption on claim tx
    18. Verify USDC received back
    """
    web3 = web3_write
    vault: OstiumVault = create_vault_instance_autodetect(web3, "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")
    assert vault.version == OstiumVersion.v1_5

    deposit_manager = vault.get_deposit_manager()
    assert isinstance(deposit_manager, OstiumV15DepositManager)

    amount = Decimal(100)

    # 1. Approve USDC
    tx_hash = usdc.approve(vault.address, amount).transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # 2. Create deposit request
    deposit_request = deposit_manager.create_deposit_request(test_user, amount=amount)
    assert isinstance(deposit_request, OstiumDepositRequest)

    # 3. Broadcast requestDeposit
    tx_hashes = []
    for func in deposit_request.funcs:
        tx_hash = func.transact({"from": test_user, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)
        tx_hashes.append(tx_hash)

    deposit_ticket = deposit_request.parse_deposit_transaction(tx_hashes)
    assert isinstance(deposit_ticket, OstiumDepositTicket)
    assert deposit_ticket.settlement_id > 0
    assert deposit_ticket.owner == test_user

    # 4. Check PENDING status
    status = deposit_manager.get_deposit_ticket_status(deposit_ticket)
    assert status == OSTIUM_REQUEST_STATUS_PENDING

    # 5. Check in-progress
    assert deposit_manager.is_deposit_in_progress(test_user) is True

    # 6. Force settlement
    force_ostium_v15_settlement(vault, test_user)

    # 7. Check CLAIMABLE status
    status = deposit_manager.get_deposit_ticket_status(deposit_ticket)
    assert status == OSTIUM_REQUEST_STATUS_CLAIMABLE
    assert deposit_manager.can_finish_deposit(deposit_ticket) is True

    # 8. Claim deposit
    claim_func = deposit_manager.finish_deposit(deposit_ticket)
    claim_tx_hash = claim_func.transact({"from": test_user, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, claim_tx_hash)

    # 9. Analyse deposit claim
    analysis = deposit_manager.analyse_deposit(claim_tx_hash, deposit_ticket)
    assert analysis.share_count > 0
    assert analysis.denomination_amount > 0

    # 10. Verify OLP shares received
    share_token = vault.share_token
    shares = share_token.fetch_balance_of(test_user)
    assert shares > 0

    # 11. Create redemption request
    redemption_request = deposit_manager.create_redemption_request(
        owner=test_user,
        shares=shares,
    )
    assert isinstance(redemption_request, OstiumRedemptionRequest)

    # 12. Broadcast requestWithdraw
    tx_hashes = []
    for func in redemption_request.funcs:
        tx_hash = func.transact({"from": test_user, "gas": 1_000_000})
        assert_transaction_success_with_explanation(web3, tx_hash)
        tx_hashes.append(tx_hash)

    redemption_ticket = redemption_request.parse_redeem_transaction(tx_hashes)
    assert isinstance(redemption_ticket, OstiumRedemptionTicket)
    assert redemption_ticket.settlement_id > 0

    # 13. Check PENDING status
    status = deposit_manager.get_redemption_ticket_status(redemption_ticket)
    assert status == OSTIUM_REQUEST_STATUS_PENDING

    # 14. Force settlement(s) for withdrawal
    # withdrawSettlementDelay may require multiple settlements
    withdraw_target = vault.vault_contract.functions.targetSettlementId(False).call()
    last_id = vault.vault_contract.functions.lastSettlementId().call()
    settlements_needed = max(withdraw_target - last_id, 1)
    for _ in range(settlements_needed):
        force_ostium_v15_settlement(vault, test_user)

    # 15. Check CLAIMABLE status
    status = deposit_manager.get_redemption_ticket_status(redemption_ticket)
    assert status == OSTIUM_REQUEST_STATUS_CLAIMABLE
    assert deposit_manager.can_finish_redeem(redemption_ticket) is True

    # 16. Claim withdrawal
    claim_func = deposit_manager.finish_redemption(redemption_ticket)
    claim_tx_hash = claim_func.transact({"from": test_user, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, claim_tx_hash)

    # 17. Analyse redemption claim
    analysis = deposit_manager.analyse_redemption(claim_tx_hash, redemption_ticket)
    assert analysis.denomination_amount > 0

    # 18. Verify USDC received and shares gone
    remaining_shares = share_token.fetch_balance_of(test_user)
    assert remaining_shares == 0

    usdc_balance = usdc.fetch_balance_of(test_user)
    # Should get back approximately what we deposited (minus any fees/slippage)
    assert usdc_balance > Decimal(9_900)  # Started with 10k, deposited 100, should get ~100 back

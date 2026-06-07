"""Gains vault testing helpers.

Includes Anvil-based simulation helpers for testing the Ostium V1.5
async deposit/settlement/claim/withdraw cycle without real funds.

See also ``scripts/erc-4626/ostium-v15.py`` for the CLI script that
uses these helpers in ``SIMULATE=true`` mode.
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.provider.anvil import mine
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import to_unix_timestamp, from_unix_timestamp

logger = logging.getLogger(__name__)


def force_next_gains_epoch(
    vault: GainsVault,
    any_account: HexAddress,
    padding_seconds: int = 1,
    gas_limit=3_000_000,
):
    """Advance Gains vault to a next epoch by using Anvil hacks.

    :param any_account:
        Burn gas
    """

    assert isinstance(vault, GainsVault), f"Expected GainsVault, got {type(vault)}"

    web3 = vault.web3

    current_epoch = vault.fetch_current_epoch()

    # Full delay
    current_epoch_start = vault.vault_contract.functions.currentEpochStart().call()
    epoch_duration = vault.open_pnl_contract.functions.requestsStart().call() + (vault.open_pnl_contract.functions.requestsEvery().call() * vault.open_pnl_contract.functions.requestsCount().call())
    next_epoch = current_epoch_start + epoch_duration

    # How loong until the epoch is cooked
    unix_timestamp = next_epoch + padding_seconds

    # Handle mining old blocks
    current_block_time = web3.eth.get_block("latest")["timestamp"]
    if current_block_time >= unix_timestamp:
        unix_timestamp = current_block_time + 1

    timestamp = from_unix_timestamp(unix_timestamp)

    logger.info(
        "Current epoch: #%d (%s / %s), next epoch start at: %s, epoch duration %s",
        current_epoch,
        from_unix_timestamp(current_epoch_start),
        current_epoch_start,
        timestamp,
        datetime.timedelta(seconds=epoch_duration),
    )

    mine(
        web3,
        timestamp=int(to_unix_timestamp(timestamp)),
    )

    tx_hash = vault.open_pnl_contract.functions.forceNewEpoch().transact({"from": any_account, "gas": gas_limit})
    assert_transaction_success_with_explanation(web3, tx_hash)


def force_ostium_v15_settlement(
    vault: "eth_defi.erc_4626.vault_protocol.gains.vault.OstiumVault",
    any_account: HexAddress,
    padding_seconds: int = 1,
    gas_limit: int = 3_000_000,
):
    """Force a settlement on Ostium V1.5 by advancing Anvil time and calling ``tryNewSettlement()``.

    ``tryNewSettlement()`` is public and permissionless — it executes when
    ``block.timestamp >= lastSettlementTs + maxSettlementInterval``.

    :param vault:
        Ostium V1.5 vault instance.

    :param any_account:
        Any account to pay gas for the transaction.

    :param padding_seconds:
        Extra seconds past the settlement threshold.
    """
    from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault, OstiumVersion

    assert isinstance(vault, OstiumVault), f"Expected OstiumVault, got {type(vault)}"
    assert vault.version == OstiumVersion.v1_5, f"Expected V1.5 vault, got {vault.version}"

    web3 = vault.web3
    contract = vault.vault_contract

    last_settlement_ts = contract.functions.lastSettlementTs().call()
    max_interval = contract.functions.maxSettlementInterval().call()
    last_settlement_id = contract.functions.lastSettlementId().call()

    target_ts = last_settlement_ts + max_interval + padding_seconds

    # Ensure we advance past current block time
    current_block_time = web3.eth.get_block("latest")["timestamp"]
    if current_block_time >= target_ts:
        target_ts = current_block_time + 1

    logger.info(
        "Forcing V1.5 settlement: lastSettlementId=%d, lastSettlementTs=%s, maxInterval=%ds, advancing to %s",
        last_settlement_id,
        from_unix_timestamp(last_settlement_ts),
        max_interval,
        from_unix_timestamp(target_ts),
    )

    mine(
        web3,
        timestamp=target_ts,
    )

    tx_hash = contract.functions.tryNewSettlement().transact({"from": any_account, "gas": gas_limit})
    assert_transaction_success_with_explanation(web3, tx_hash)

    new_settlement_id = contract.functions.lastSettlementId().call()
    logger.info("Settlement completed: lastSettlementId %d -> %d", last_settlement_id, new_settlement_id)


@dataclass(slots=True)
class OstiumSimulationResult:
    """Result of a simulated Ostium V1.5 deposit/withdrawal cycle."""

    #: USDC deposited
    deposit_amount: Decimal
    #: OLP shares received after claim
    shares_received: Decimal
    #: Settlement price used
    share_price: Decimal
    #: USDC received after withdrawal claim
    usdc_withdrawn: Decimal
    #: Number of settlements forced
    settlements_forced: int


def setup_ostium_simulation(
    json_rpc_url: str,
    vault_address: str = "0x20d419a8e12c45f88fda7c5760bb6923cee27f98",
    fund_amount: Decimal = Decimal(100),
) -> tuple[Web3, "HotWallet", "AnvilLaunch", "OstiumVault"]:
    """Set up an Anvil fork environment for Ostium V1.5 simulation.

    Creates an Anvil fork of Arbitrum, a funded test wallet, and
    an Ostium vault instance.

    :param json_rpc_url:
        Arbitrum RPC URL to fork from.

    :param vault_address:
        Ostium vault address.

    :param fund_amount:
        USDC amount to fund the test wallet with.

    :return:
        Tuple of (web3, hot_wallet, anvil_launch, vault).
    """
    from eth_defi.erc_4626.classification import create_vault_instance_autodetect
    from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault, OstiumVersion
    from eth_defi.hotwallet import HotWallet
    from eth_defi.provider.anvil import fork_network_anvil
    from eth_defi.provider.multi_provider import create_multi_provider_web3
    from eth_defi.token import fetch_erc20_details, USDC_NATIVE_TOKEN, USDC_WHALE

    chain_id = 42161
    usdc_whale = USDC_WHALE[chain_id]

    print("Starting Anvil fork of Arbitrum...")
    anvil_launch = fork_network_anvil(
        json_rpc_url,
        unlocked_addresses=[usdc_whale],
    )

    web3 = create_multi_provider_web3(
        anvil_launch.json_rpc_url,
        default_http_timeout=(3.0, 180.0),
    )

    # Create and fund test wallet
    hot_wallet = HotWallet.create_for_testing(web3, test_account_n=0, eth_amount=0)
    hot_wallet.sync_nonce(web3)

    # Fund with ETH for gas
    web3.provider.make_request("anvil_setBalance", [hot_wallet.address, hex(10 * 10**18)])
    print(f"Test wallet: {hot_wallet.address}")
    print(f"  Funded with 10 ETH for gas")

    # Fund with USDC from whale
    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[chain_id])
    raw_amount = usdc.convert_to_raw(fund_amount)
    tx_hash = usdc.contract.functions.transfer(
        hot_wallet.address,
        raw_amount,
    ).transact({"from": usdc_whale, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    print(f"  Funded with {fund_amount} USDC from whale {usdc_whale}")

    # Create vault instance
    vault: OstiumVault = create_vault_instance_autodetect(web3, vault_address)
    assert vault.version == OstiumVersion.v1_5, f"Expected V1.5, got {vault.version}"

    return web3, hot_wallet, anvil_launch, vault


def simulate_ostium_v15_cycle(
    web3: Web3,
    hot_wallet: "HotWallet",
    vault: "OstiumVault",
    deposit_amount: Decimal = Decimal(50),
) -> OstiumSimulationResult:
    """Simulate a full Ostium V1.5 deposit/settlement/claim/withdraw cycle on Anvil.

    Runs the complete async lifecycle:

    1. Approve USDC to vault
    2. Call ``requestDeposit(amount)``
    3. Force settlement via ``tryNewSettlement()``
    4. Claim deposit via ``claimDeposit(settlementId)``
    5. Verify OLP shares received
    6. Call ``requestWithdraw(shares)``
    7. Force settlement(s) for withdrawal
    8. Claim withdrawal via ``claimWithdraw(settlementId)``
    9. Verify USDC returned

    :param web3:
        Web3 connected to an Anvil fork.

    :param hot_wallet:
        Funded test wallet.

    :param vault:
        Ostium V1.5 vault instance.

    :param deposit_amount:
        USDC amount to deposit.

    :return:
        Simulation result with amounts and prices.
    """
    from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import (
        OstiumV15DepositManager,
    )
    from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

    owner = hot_wallet.address
    deposit_manager: OstiumV15DepositManager = vault.get_deposit_manager()

    usdc_before = vault.denomination_token.fetch_balance_of(owner)
    print(f"\n{'─' * 60}")
    print(f"SIMULATING DEPOSIT/WITHDRAWAL CYCLE")
    print(f"{'─' * 60}")
    print(f"Deposit amount: {deposit_amount} USDC")
    print(f"USDC balance:   {usdc_before} USDC")

    # 1. Approve USDC
    print(f"\n  Step 1: Approve {deposit_amount} USDC...")
    approve_func = vault.denomination_token.approve(vault.address, deposit_amount)
    tx_hash = approve_func.transact({"from": owner, "gas": 100_000})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # 2. Request deposit
    print(f"  Step 2: requestDeposit({deposit_amount} USDC)...")
    deposit_request = deposit_manager.create_deposit_request(owner, amount=deposit_amount)
    tx_hash = deposit_request.funcs[0].transact({"from": owner, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    deposit_ticket = deposit_request.parse_deposit_transaction([tx_hash])
    print(f"           Settlement ID: {deposit_ticket.settlement_id}")

    # 3. Force settlement
    print(f"  Step 3: Forcing settlement...")
    force_ostium_v15_settlement(vault, owner)

    status = deposit_manager.get_deposit_request_status(deposit_ticket)
    assert status == AsyncVaultRequestStatus.claimable, f"Expected claimable, got {status.value}"

    # 4. Claim deposit
    print(f"  Step 4: claimDeposit({deposit_ticket.settlement_id})...")
    claim_func = deposit_manager.finish_deposit(deposit_ticket)
    claim_tx = claim_func.transact({"from": owner, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, claim_tx)

    analysis = deposit_manager.analyse_deposit(claim_tx, deposit_ticket)
    shares_received = analysis.share_count
    share_price = analysis.get_share_price()
    print(f"           Received {shares_received} oLP (price: {share_price} USDC/oLP)")

    # 5. Verify shares
    share_balance = vault.share_token.fetch_balance_of(owner)
    assert share_balance > 0, f"No shares received"
    print(f"           Share balance: {share_balance} oLP")

    # 6. Request withdrawal
    print(f"  Step 5: requestWithdraw({share_balance} oLP)...")
    redeem_request = deposit_manager.create_redemption_request(owner, shares=share_balance)
    tx_hash = redeem_request.funcs[0].transact({"from": owner, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    redeem_ticket = redeem_request.parse_redeem_transaction([tx_hash])
    print(f"           Settlement ID: {redeem_ticket.settlement_id}")

    # 7. Force settlement(s) for withdrawal
    settlements_forced = 0
    withdraw_target = vault.vault_contract.functions.targetSettlementId(False).call()
    last_id = vault.vault_contract.functions.lastSettlementId().call()
    settlements_needed = max(withdraw_target - last_id, 1)
    print(f"  Step 6: Forcing {settlements_needed} settlement(s) for withdrawal...")
    for _ in range(settlements_needed):
        force_ostium_v15_settlement(vault, owner)
        settlements_forced += 1

    status = deposit_manager.get_redemption_request_status(redeem_ticket)
    assert status == AsyncVaultRequestStatus.claimable, f"Expected claimable, got {status.value}"

    # 8. Claim withdrawal
    print(f"  Step 7: claimWithdraw({redeem_ticket.settlement_id})...")
    claim_func = deposit_manager.finish_redemption(redeem_ticket)
    claim_tx = claim_func.transact({"from": owner, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, claim_tx)

    redeem_analysis = deposit_manager.analyse_redemption(claim_tx, redeem_ticket)
    usdc_withdrawn = redeem_analysis.denomination_amount
    print(f"           Received {usdc_withdrawn} USDC")

    # 9. Verify final state
    final_shares = vault.share_token.fetch_balance_of(owner)
    usdc_after = vault.denomination_token.fetch_balance_of(owner)

    assert final_shares == 0, f"Shares remaining: {final_shares}"

    print(f"\n{'─' * 60}")
    print(f"SIMULATION COMPLETE")
    print(f"{'─' * 60}")
    print(f"Deposited:           {deposit_amount} USDC")
    print(f"Shares received:     {shares_received} oLP")
    print(f"Share price:         {share_price} USDC/oLP")
    print(f"USDC withdrawn:      {usdc_withdrawn} USDC")
    print(f"Settlements forced:  {settlements_forced + 1}")
    print(f"USDC before:         {usdc_before} USDC")
    print(f"USDC after:          {usdc_after} USDC")
    diff = usdc_after - usdc_before
    print(f"Net change:          {diff:+} USDC")
    print(f"{'─' * 60}")

    return OstiumSimulationResult(
        deposit_amount=deposit_amount,
        shares_received=shares_received,
        share_price=share_price,
        usdc_withdrawn=usdc_withdrawn,
        settlements_forced=settlements_forced + 1,
    )

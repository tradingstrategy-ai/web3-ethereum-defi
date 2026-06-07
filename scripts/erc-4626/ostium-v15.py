"""Ostium V1.5 async vault operations: status, deposit, and withdraw.

Supports the full async request/settle/claim lifecycle including reclaim
after failed settlements. All transaction-sending actions require y/n
confirmation before broadcast.

Environment variables:
    JSON_RPC_ARBITRUM   Arbitrum RPC URL (space-separated fallback format)
    ACTION              One of: status, deposit, withdraw (default: status)
    PRIVATE_KEY         Private key for signing (required for deposit/withdraw)
    VAULT_ADDRESS       Ostium vault address (default: OLP vault)
    OWNER_ADDRESS       Address to check status for (status action only,
                        defaults to PRIVATE_KEY address if set)
    AMOUNT              USDC amount for deposit, OLP share amount for withdraw
    SETTLEMENT_ID       Settlement ID for --claim / --reclaim modes

Usage:
    # Check vault state and owner status
    ACTION=status poetry run python scripts/erc-4626/ostium-v15.py
    ACTION=status OWNER_ADDRESS=0x... poetry run python scripts/erc-4626/ostium-v15.py

    # Request a deposit
    ACTION=deposit AMOUNT=100 poetry run python scripts/erc-4626/ostium-v15.py

    # Claim after settlement
    ACTION=deposit SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15.py --claim

    # Reclaim after failed settlement
    ACTION=deposit SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15.py --reclaim

    # Request a withdrawal
    ACTION=withdraw AMOUNT=50 poetry run python scripts/erc-4626/ostium-v15.py

    # Claim withdrawal after settlement
    ACTION=withdraw SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15.py --claim
"""

import logging
import os
import sys
from decimal import Decimal

from hexbytes import HexBytes
from tabulate import tabulate
from web3._utils.events import EventLogErrorFlags

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import (
    OSTIUM_REQUEST_STATUS_NONE,
    OSTIUM_REQUEST_STATUS_PENDING,
    OSTIUM_REQUEST_STATUS_CLAIMABLE,
    OSTIUM_REQUEST_STATUS_RECLAIMABLE,
    OstiumDepositTicket,
    OstiumRedemptionTicket,
    OstiumV15DepositManager,
)
from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault, OstiumVersion
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

STATUS_NAMES = {
    OSTIUM_REQUEST_STATUS_NONE: "NONE",
    OSTIUM_REQUEST_STATUS_PENDING: "PENDING",
    OSTIUM_REQUEST_STATUS_CLAIMABLE: "CLAIMABLE",
    OSTIUM_REQUEST_STATUS_RECLAIMABLE: "RECLAIMABLE",
}


def confirm(prompt: str) -> bool:
    """Ask for y/n confirmation before sending a transaction."""
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer == "y"


def do_status(vault: OstiumVault, owner_address: str | None):
    """Display vault settlement state and per-address request status."""
    contract = vault.vault_contract

    last_settlement_id = contract.functions.lastSettlementId().call()
    deposit_target = contract.functions.targetSettlementId(True).call()
    withdraw_target = contract.functions.targetSettlementId(False).call()
    last_ts = contract.functions.lastSettlementTs().call()
    max_interval = contract.functions.maxSettlementInterval().call()

    print(f"Vault: {vault.name} ({vault.address})")
    print(f"Last settlement ID: {last_settlement_id}")
    print(f"Deposit target settlement ID: {deposit_target}")
    print(f"Withdraw target settlement ID: {withdraw_target}")
    print(f"Last settlement timestamp: {last_ts}")
    print(f"Max settlement interval: {max_interval}s ({max_interval / 3600:.1f}h)")
    print()

    if not owner_address:
        print("Set OWNER_ADDRESS (or PRIVATE_KEY) to check deposit/withdrawal status for a specific address.")
        return

    print(f"Status for owner: {owner_address}")
    print()

    rows = []
    for sid in range(max(1, last_settlement_id - 5), deposit_target + 1):
        dep_status = contract.functions.getDepositStatus(owner_address, sid).call()
        wd_status = contract.functions.getWithdrawStatus(owner_address, sid).call()
        if dep_status != OSTIUM_REQUEST_STATUS_NONE or wd_status != OSTIUM_REQUEST_STATUS_NONE:
            rows.append(
                {
                    "Settlement ID": sid,
                    "Deposit": STATUS_NAMES.get(dep_status, f"UNKNOWN({dep_status})"),
                    "Withdraw": STATUS_NAMES.get(wd_status, f"UNKNOWN({wd_status})"),
                }
            )

    if rows:
        print(tabulate(rows, headers="keys", tablefmt="simple"))
    else:
        print("No active deposit or withdrawal requests found.")


def do_deposit(vault: OstiumVault, deposit_manager: OstiumV15DepositManager, hot_wallet: HotWallet, web3):
    """Handle deposit request, claim, or reclaim."""
    owner = hot_wallet.address
    vault_address = vault.address
    claim_mode = "--claim" in sys.argv
    reclaim_mode = "--reclaim" in sys.argv

    if claim_mode or reclaim_mode:
        settlement_id = int(os.environ["SETTLEMENT_ID"])
        ticket = OstiumDepositTicket(
            vault_address=vault_address,
            owner=owner,
            to=owner,
            raw_amount=1,
            tx_hash=HexBytes(b"\x00" * 32),
            gas_used=0,
            block_number=0,
            block_timestamp=None,
            settlement_id=settlement_id,
        )

        status = deposit_manager.get_deposit_request_status(ticket)
        print(f"Deposit status for settlement {settlement_id}: {status.value}")

        if reclaim_mode:
            if status != AsyncVaultRequestStatus.reclaimable:
                print(f"Cannot reclaim — status is {status.value}, not reclaimable")
                sys.exit(1)
            if not confirm(f"Reclaim USDC from failed deposit settlement {settlement_id}?"):
                sys.exit(0)
            reclaim_func = deposit_manager.reclaim_deposit(ticket)
            tx_hash = hot_wallet.transact_with_contract(reclaim_func, gas=1_000_000)
            assert_transaction_success_with_explanation(web3, tx_hash)
            print(f"Reclaimed USDC from failed settlement {settlement_id}")
            print(f"Tx hash: {tx_hash.hex()}")
        elif status == AsyncVaultRequestStatus.claimable:
            if not confirm(f"Claim deposit from settlement {settlement_id}?"):
                sys.exit(0)
            claim_func = deposit_manager.finish_deposit(ticket)
            tx_hash = hot_wallet.transact_with_contract(claim_func, gas=1_000_000)
            assert_transaction_success_with_explanation(web3, tx_hash)

            ticket_for_analysis = OstiumDepositTicket(
                vault_address=vault_address,
                owner=owner,
                to=owner,
                raw_amount=1,
                tx_hash=HexBytes(tx_hash),
                gas_used=0,
                block_number=0,
                block_timestamp=None,
                settlement_id=settlement_id,
            )
            analysis = deposit_manager.analyse_deposit(tx_hash, ticket_for_analysis)
            print(f"Claimed {analysis.share_count} shares ({analysis.denomination_amount} USDC equivalent)")
        elif status == AsyncVaultRequestStatus.reclaimable:
            print(f"Settlement failed. Reclaim with: ACTION=deposit SETTLEMENT_ID={settlement_id} python {sys.argv[0]} --reclaim")
        else:
            print(f"Cannot claim yet. Status: {status.value}")
    else:
        amount = Decimal(os.environ["AMOUNT"])
        usdc = fetch_erc20_details(web3, vault.denomination_token.address)

        usdc_balance = usdc.fetch_balance_of(owner)
        print(f"Wallet: {owner}")
        print(f"USDC balance: {usdc_balance}")
        print(f"Deposit amount: {amount} USDC")

        if not confirm(f"Approve and request deposit of {amount} USDC to Ostium vault?"):
            sys.exit(0)

        approve_func = usdc.approve(vault_address, amount)
        tx_hash = hot_wallet.transact_with_contract(approve_func, gas=100_000)
        assert_transaction_success_with_explanation(web3, tx_hash)

        deposit_request = deposit_manager.create_deposit_request(owner, amount=amount)
        tx_hash = hot_wallet.transact_with_contract(deposit_request.funcs[0], gas=1_000_000)
        assert_transaction_success_with_explanation(web3, tx_hash)

        ticket = deposit_request.parse_deposit_transaction([tx_hash])
        print(f"Deposit requested: {amount} USDC")
        print(f"Settlement ID: {ticket.settlement_id}")
        print(f"Tx hash: {tx_hash.hex()}")
        print(f"\nAfter settlement, claim with: ACTION=deposit SETTLEMENT_ID={ticket.settlement_id} python {sys.argv[0]} --claim")


def do_withdraw(vault: OstiumVault, deposit_manager: OstiumV15DepositManager, hot_wallet: HotWallet, web3):
    """Handle withdrawal request, claim, or reclaim."""
    owner = hot_wallet.address
    vault_address = vault.address
    claim_mode = "--claim" in sys.argv
    reclaim_mode = "--reclaim" in sys.argv

    if claim_mode or reclaim_mode:
        settlement_id = int(os.environ["SETTLEMENT_ID"])
        ticket = OstiumRedemptionTicket(
            vault_address=vault_address,
            owner=owner,
            to=owner,
            raw_shares=1,
            tx_hash=HexBytes(b"\x00" * 32),
            settlement_id=settlement_id,
        )

        status = deposit_manager.get_redemption_request_status(ticket)
        print(f"Withdrawal status for settlement {settlement_id}: {status.value}")

        if reclaim_mode:
            if status != AsyncVaultRequestStatus.reclaimable:
                print(f"Cannot reclaim — status is {status.value}, not reclaimable")
                sys.exit(1)
            if not confirm(f"Reclaim OLP shares from failed withdrawal settlement {settlement_id}?"):
                sys.exit(0)
            reclaim_func = deposit_manager.reclaim_withdrawal(ticket)
            tx_hash = hot_wallet.transact_with_contract(reclaim_func, gas=1_000_000)
            assert_transaction_success_with_explanation(web3, tx_hash)
            print(f"Reclaimed OLP shares from failed settlement {settlement_id}")
            print(f"Tx hash: {tx_hash.hex()}")
        elif status == AsyncVaultRequestStatus.claimable:
            if not confirm(f"Claim withdrawal from settlement {settlement_id}?"):
                sys.exit(0)
            claim_func = deposit_manager.finish_redemption(ticket)
            tx_hash = hot_wallet.transact_with_contract(claim_func, gas=1_000_000)
            assert_transaction_success_with_explanation(web3, tx_hash)

            receipt = web3.eth.get_transaction_receipt(tx_hash)
            logs = vault.vault_contract.events.WithdrawClaimedV2().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
            if logs:
                raw_assets = logs[0]["args"]["assets"]
                usdc_amount = vault.denomination_token.convert_to_decimals(raw_assets)
                print(f"Claimed {usdc_amount} USDC")
            else:
                print("Claim succeeded but could not parse WithdrawClaimedV2 event")
        elif status == AsyncVaultRequestStatus.reclaimable:
            print(f"Settlement failed. Reclaim with: ACTION=withdraw SETTLEMENT_ID={settlement_id} python {sys.argv[0]} --reclaim")
        else:
            print(f"Cannot claim yet. Status: {status.value}")
    else:
        amount = Decimal(os.environ["AMOUNT"])
        share_token = vault.share_token

        share_balance = share_token.fetch_balance_of(owner)
        print(f"Wallet: {owner}")
        print(f"OLP share balance: {share_balance}")
        print(f"Withdrawal amount: {amount} OLP shares")

        if not confirm(f"Request withdrawal of {amount} OLP shares from Ostium vault?"):
            sys.exit(0)

        redemption_request = deposit_manager.create_redemption_request(owner, shares=amount)
        tx_hash = hot_wallet.transact_with_contract(redemption_request.funcs[0], gas=1_000_000)
        assert_transaction_success_with_explanation(web3, tx_hash)

        ticket = redemption_request.parse_redeem_transaction([tx_hash])
        print(f"Withdrawal requested: {amount} OLP shares")
        print(f"Settlement ID: {ticket.settlement_id}")
        print(f"Tx hash: {tx_hash.hex()}")
        print(f"\nAfter settlement, claim with: ACTION=withdraw SETTLEMENT_ID={ticket.settlement_id} python {sys.argv[0]} --claim")


# --- Main ---

action = os.environ.get("ACTION", "status").lower()
vault_address = os.environ.get("VAULT_ADDRESS", "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")

web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
vault: OstiumVault = create_vault_instance_autodetect(web3, vault_address)
assert vault.version == OstiumVersion.v1_5, f"Expected V1.5, got {vault.version}"

if action == "status":
    owner_address = os.environ.get("OWNER_ADDRESS")
    if not owner_address and os.environ.get("PRIVATE_KEY"):
        owner_address = HotWallet.from_private_key(os.environ["PRIVATE_KEY"]).address
    do_status(vault, owner_address)
elif action in ("deposit", "withdraw"):
    private_key = os.environ["PRIVATE_KEY"]
    hot_wallet = HotWallet.from_private_key(private_key)
    hot_wallet.sync_nonce(web3)
    deposit_manager: OstiumV15DepositManager = vault.get_deposit_manager()

    if action == "deposit":
        do_deposit(vault, deposit_manager, hot_wallet, web3)
    else:
        do_withdraw(vault, deposit_manager, hot_wallet, web3)
else:
    print(f"Unknown ACTION: {action}. Use: status, deposit, withdraw")
    sys.exit(1)

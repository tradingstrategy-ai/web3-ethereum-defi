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


def broadcast(web3, hot_wallet: HotWallet, func, description: str, gas: int = 500_000) -> HexBytes:
    """Sign, broadcast, and wait for a contract call. Returns tx hash."""
    signed_tx = hot_wallet.sign_bound_call_with_new_nonce(func, tx_params={"gas": gas}, web3=web3, fill_gas_price=True)
    print(f"  Broadcasting: {description}")
    print(f"  TX hash: {signed_tx.hash.hex()}")
    web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(signed_tx.hash, timeout=120)
    assert receipt["status"] == 1, f"Transaction reverted: {signed_tx.hash.hex()}"
    print(f"  Gas used: {receipt['gasUsed']:,}")
    return signed_tx.hash


def print_vault_state(vault: OstiumVault, web3, owner_address: str | None = None):
    """Print vault state summary at the start of every action.

    Shows chain info, vault TVL, share price, settlement state,
    deposit/redemption status, and owner-specific token balances
    and active settlement tickets.
    """
    from eth_defi.chain import get_chain_name
    from eth_defi.utils import from_unix_timestamp

    block = web3.eth.block_number
    chain_id = web3.eth.chain_id
    chain_name = get_chain_name(chain_id)
    contract = vault.vault_contract

    # ── Chain and vault ──────────────────────────────────────────────
    print("=" * 70)
    print(f"OSTIUM V1.5 VAULT")
    print("=" * 70)

    print(f"\nChain:          {chain_name} (chain ID: {chain_id})")
    print(f"Block:          {block:,}")
    print(f"Vault:          {vault.name}")
    print(f"Address:        {vault.address}")
    print(f"Denomination:   {vault.denomination_token.symbol} ({vault.denomination_token.address})")
    print(f"Share token:    {vault.share_token.symbol} ({vault.share_token.address})")

    # ── TVL and pricing ──────────────────────────────────────────────
    total_assets = vault.fetch_total_assets(block)
    total_supply = vault.fetch_total_supply(block)
    share_price = vault.fetch_share_price(block)
    deposit_closed = vault.fetch_deposit_closed_reason()
    redemption_closed = vault.fetch_redemption_closed_reason()

    print(f"\n{'─' * 70}")
    print(f"TVL (total assets):  {total_assets} {vault.denomination_token.symbol}")
    print(f"Total supply:        {total_supply} {vault.share_token.symbol}")
    print(f"Share price:         {share_price} {vault.denomination_token.symbol}/{vault.share_token.symbol}")
    print(f"Deposits:            {'OPEN' if not deposit_closed else deposit_closed}")
    print(f"Redemptions:         {'OPEN' if not redemption_closed else redemption_closed}")

    # ── Settlement state ─────────────────────────────────────────────
    last_settlement_id = contract.functions.lastSettlementId().call()
    deposit_target = contract.functions.targetSettlementId(True).call()
    withdraw_target = contract.functions.targetSettlementId(False).call()
    last_ts = contract.functions.lastSettlementTs().call()
    max_interval = contract.functions.maxSettlementInterval().call()
    last_settlement_dt = from_unix_timestamp(last_ts)

    print(f"\n{'─' * 70}")
    print(f"Settlement state:")
    print(f"  Last settlement ID:    {last_settlement_id}")
    print(f"  Deposit target ID:     {deposit_target}")
    print(f"  Withdraw target ID:    {withdraw_target}")
    print(f"  Last settlement:       {last_settlement_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Max interval:          {max_interval}s ({max_interval / 3600:.1f}h)")

    # ── Owner balances and tickets ───────────────────────────────────
    if owner_address:
        eth_balance = web3.eth.get_balance(owner_address)
        eth_human = web3.from_wei(eth_balance, "ether")
        usdc_balance = vault.denomination_token.fetch_balance_of(owner_address)
        share_balance = vault.share_token.fetch_balance_of(owner_address)
        share_value = share_balance * share_price if share_price else Decimal(0)

        print(f"\n{'─' * 70}")
        print(f"Owner: {owner_address}")
        print(f"  ETH balance:           {eth_human} ETH")
        print(f"  {vault.denomination_token.symbol} balance:          {usdc_balance} {vault.denomination_token.symbol}")
        print(f"  {vault.share_token.symbol} balance:           {share_balance} {vault.share_token.symbol}")
        print(f"  Share value:           ~{share_value:.2f} {vault.denomination_token.symbol}")

        # Active tickets
        deposit_manager = vault.get_deposit_manager()
        has_pending_deposit = deposit_manager.is_deposit_in_progress(owner_address)
        has_pending_redeem = deposit_manager.is_redemption_in_progress(owner_address)
        print(f"  Pending deposit:       {'YES' if has_pending_deposit else 'no'}")
        print(f"  Pending withdrawal:    {'YES' if has_pending_redeem else 'no'}")

        # Scan recent settlement IDs for active requests
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
            print(f"\n  Active tickets:")
            print("  " + tabulate(rows, headers="keys", tablefmt="simple").replace("\n", "\n  "))
    else:
        print(f"\n{'─' * 70}")
        print("Set OWNER_ADDRESS or PRIVATE_KEY to see owner-specific balances and tickets.")

    print("=" * 70)
    print()


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
            tx_hash = broadcast(web3, hot_wallet, reclaim_func, f"reclaimDeposit({settlement_id})", gas=1_000_000)
            print(f"Reclaimed USDC from failed settlement {settlement_id}")
        elif status == AsyncVaultRequestStatus.claimable:
            if not confirm(f"Claim deposit from settlement {settlement_id}?"):
                sys.exit(0)
            claim_func = deposit_manager.finish_deposit(ticket)
            tx_hash = broadcast(web3, hot_wallet, claim_func, f"claimDeposit({settlement_id})", gas=1_000_000)

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

        if not confirm(f"Approve and request deposit of {amount} USDC to Ostium vault?"):
            sys.exit(0)

        approve_func = vault.denomination_token.approve(vault_address, amount)
        broadcast(web3, hot_wallet, approve_func, f"Approve {amount} USDC to Ostium vault")

        deposit_request = deposit_manager.create_deposit_request(owner, amount=amount)
        tx_hash = broadcast(web3, hot_wallet, deposit_request.funcs[0], f"requestDeposit({amount} USDC)", gas=1_000_000)

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
            tx_hash = broadcast(web3, hot_wallet, reclaim_func, f"reclaimWithdraw({settlement_id})", gas=1_000_000)
            print(f"Reclaimed OLP shares from failed settlement {settlement_id}")
        elif status == AsyncVaultRequestStatus.claimable:
            if not confirm(f"Claim withdrawal from settlement {settlement_id}?"):
                sys.exit(0)
            claim_func = deposit_manager.finish_redemption(ticket)
            tx_hash = broadcast(web3, hot_wallet, claim_func, f"claimWithdraw({settlement_id})", gas=1_000_000)

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

        if not confirm(f"Request withdrawal of {amount} OLP shares from Ostium vault?"):
            sys.exit(0)

        redemption_request = deposit_manager.create_redemption_request(owner, shares=amount)
        tx_hash = broadcast(web3, hot_wallet, redemption_request.funcs[0], f"requestWithdraw({amount} OLP)", gas=1_000_000)

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

# Resolve owner address for vault state display
owner_address = os.environ.get("OWNER_ADDRESS")
if not owner_address and os.environ.get("PRIVATE_KEY"):
    owner_address = HotWallet.from_private_key(os.environ["PRIVATE_KEY"]).address

# Always print vault state at the start
print_vault_state(vault, web3, owner_address)

if action == "status":
    pass  # Vault state already printed above
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

"""Request or claim an async withdrawal from the Ostium V1.5 vault.

Usage:
    # Request a withdrawal (calls requestWithdraw with OLP shares)
    AMOUNT=50 poetry run python scripts/erc-4626/ostium-v15-withdraw.py

    # Claim a withdrawal after settlement
    SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15-withdraw.py --claim

Environment variables:
    JSON_RPC_ARBITRUM   Arbitrum RPC URL (space-separated fallback format)
    PRIVATE_KEY         Private key for signing (hex, with or without 0x prefix)
    VAULT_ADDRESS       Ostium vault address (default: OLP vault)
    AMOUNT              OLP share amount to withdraw (for request mode)
    SETTLEMENT_ID       Settlement ID to claim (for claim mode)
"""

import logging
import os
import sys
from decimal import Decimal

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import OstiumV15DepositManager
from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault, OstiumVersion
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

vault_address = os.environ.get("VAULT_ADDRESS", "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")
claim_mode = "--claim" in sys.argv
reclaim_mode = "--reclaim" in sys.argv

web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])

private_key = os.environ["PRIVATE_KEY"]
hot_wallet = HotWallet.from_private_key(private_key)
hot_wallet.sync_nonce(web3)
owner = hot_wallet.address

vault: OstiumVault = create_vault_instance_autodetect(web3, vault_address)
assert vault.version == OstiumVersion.v1_5, f"Expected V1.5, got {vault.version}"

deposit_manager: OstiumV15DepositManager = vault.get_deposit_manager()

if claim_mode or reclaim_mode:
    settlement_id = int(os.environ["SETTLEMENT_ID"])
    from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import OstiumRedemptionTicket
    from hexbytes import HexBytes
    from web3._utils.events import EventLogErrorFlags

    ticket = OstiumRedemptionTicket(
        vault_address=vault_address,
        owner=owner,
        to=owner,
        raw_shares=1,  # Dummy — not used by finish_redemption/get_status/reclaim
        tx_hash=HexBytes(b"\x00" * 32),
        settlement_id=settlement_id,
    )

    status = deposit_manager.get_redemption_request_status(ticket)
    print(f"Withdrawal status for settlement {settlement_id}: {status.value}")

    if reclaim_mode:
        if status != AsyncVaultRequestStatus.reclaimable:
            print(f"Cannot reclaim — status is {status.value}, not reclaimable")
            sys.exit(1)
        reclaim_func = deposit_manager.reclaim_withdrawal(ticket)
        tx_hash = hot_wallet.transact_with_contract(reclaim_func, gas=1_000_000)
        assert_transaction_success_with_explanation(web3, tx_hash)
        print(f"Reclaimed OLP shares from failed settlement {settlement_id}")
        print(f"Tx hash: {tx_hash.hex()}")
    elif status == AsyncVaultRequestStatus.claimable:
        claim_func = deposit_manager.finish_redemption(ticket)
        tx_hash = hot_wallet.transact_with_contract(claim_func, gas=1_000_000)
        assert_transaction_success_with_explanation(web3, tx_hash)

        # Read USDC amount directly from WithdrawClaimedV2 event
        # (analyse_redemption needs the original raw_shares which we don't have in claim-only mode)
        receipt = web3.eth.get_transaction_receipt(tx_hash)
        logs = vault.vault_contract.events.WithdrawClaimedV2().process_receipt(receipt, errors=EventLogErrorFlags.Discard)
        if logs:
            raw_assets = logs[0]["args"]["assets"]
            usdc_amount = vault.denomination_token.convert_to_decimals(raw_assets)
            print(f"Claimed {usdc_amount} USDC")
        else:
            print("Claim succeeded but could not parse WithdrawClaimedV2 event")
    elif status == AsyncVaultRequestStatus.reclaimable:
        print(f"Settlement failed. Reclaim with: SETTLEMENT_ID={settlement_id} python {sys.argv[0]} --reclaim")
    else:
        print(f"Cannot claim yet. Status: {status.value}")
else:
    amount = Decimal(os.environ["AMOUNT"])

    # Request withdrawal
    redemption_request = deposit_manager.create_redemption_request(owner, shares=amount)
    tx_hash = hot_wallet.transact_with_contract(redemption_request.funcs[0], gas=1_000_000)
    assert_transaction_success_with_explanation(web3, tx_hash)

    ticket = redemption_request.parse_redeem_transaction([tx_hash])
    print(f"Withdrawal requested: {amount} OLP shares")
    print(f"Settlement ID: {ticket.settlement_id}")
    print(f"Tx hash: {tx_hash.hex()}")
    print(f"\nAfter settlement, claim with: SETTLEMENT_ID={ticket.settlement_id} python {sys.argv[0]} --claim")

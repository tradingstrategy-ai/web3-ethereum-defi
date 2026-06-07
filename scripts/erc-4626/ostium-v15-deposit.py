"""Request or claim an async deposit to the Ostium V1.5 vault.

Usage:
    # Request a deposit (approves USDC and calls requestDeposit)
    AMOUNT=100 poetry run python scripts/erc-4626/ostium-v15-deposit.py

    # Claim a deposit after settlement
    SETTLEMENT_ID=42 poetry run python scripts/erc-4626/ostium-v15-deposit.py --claim

Environment variables:
    JSON_RPC_ARBITRUM   Arbitrum RPC URL (space-separated fallback format)
    PRIVATE_KEY         Private key for signing (hex, with or without 0x prefix)
    VAULT_ADDRESS       Ostium vault address (default: OLP vault)
    AMOUNT              USDC amount to deposit (for request mode)
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
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

vault_address = os.environ.get("VAULT_ADDRESS", "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")
claim_mode = "--claim" in sys.argv

web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])

private_key = os.environ["PRIVATE_KEY"]
hot_wallet = HotWallet.from_private_key(private_key)
hot_wallet.sync_nonce(web3)
owner = hot_wallet.address

vault: OstiumVault = create_vault_instance_autodetect(web3, vault_address)
assert vault.version == OstiumVersion.v1_5, f"Expected V1.5, got {vault.version}"

deposit_manager: OstiumV15DepositManager = vault.get_deposit_manager()

if claim_mode:
    settlement_id = int(os.environ["SETTLEMENT_ID"])
    from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import OstiumDepositTicket
    from hexbytes import HexBytes

    ticket = OstiumDepositTicket(
        vault_address=vault_address,
        owner=owner,
        to=owner,
        raw_amount=1,  # Dummy — not used by finish_deposit/get_status
        tx_hash=HexBytes(b"\x00" * 32),
        gas_used=0,
        block_number=0,
        block_timestamp=None,
        settlement_id=settlement_id,
    )

    status = deposit_manager.get_deposit_request_status(ticket)
    print(f"Deposit status for settlement {settlement_id}: {status.value}")

    if status == AsyncVaultRequestStatus.claimable:
        claim_func = deposit_manager.finish_deposit(ticket)
        tx_hash = hot_wallet.transact_with_contract(claim_func, gas=1_000_000)
        assert_transaction_success_with_explanation(web3, tx_hash)

        # Analyse the claim to get actual amounts
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
        print("Settlement failed. Run with SETTLEMENT_ID and --reclaim to recover funds.")
    else:
        print(f"Cannot claim yet. Status: {status.value}")
else:
    amount = Decimal(os.environ["AMOUNT"])
    usdc = fetch_erc20_details(web3, vault.denomination_token.address)

    # Approve USDC
    approve_func = usdc.approve(vault_address, amount)
    tx_hash = hot_wallet.transact_with_contract(approve_func, gas=100_000)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Request deposit
    deposit_request = deposit_manager.create_deposit_request(owner, amount=amount)
    tx_hash = hot_wallet.transact_with_contract(deposit_request.funcs[0], gas=1_000_000)
    assert_transaction_success_with_explanation(web3, tx_hash)

    ticket = deposit_request.parse_deposit_transaction([tx_hash])
    print(f"Deposit requested: {amount} USDC")
    print(f"Settlement ID: {ticket.settlement_id}")
    print(f"Tx hash: {tx_hash.hex()}")
    print(f"\nAfter settlement, claim with: SETTLEMENT_ID={ticket.settlement_id} python {sys.argv[0]} --claim")

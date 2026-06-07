"""Check Ostium V1.5 deposit/withdrawal status for an address.

Displays current settlement IDs, intervals, and request status.

Environment variables:
    JSON_RPC_ARBITRUM   Arbitrum RPC URL (space-separated fallback format)
    VAULT_ADDRESS       Ostium vault address (default: OLP vault)
    OWNER_ADDRESS       Address to check status for
"""

import logging
import os
import sys

from tabulate import tabulate

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import (
    OSTIUM_REQUEST_STATUS_NONE,
    OSTIUM_REQUEST_STATUS_PENDING,
    OSTIUM_REQUEST_STATUS_CLAIMABLE,
    OSTIUM_REQUEST_STATUS_RECLAIMABLE,
    OstiumV15DepositManager,
)
from eth_defi.erc_4626.vault_protocol.gains.vault import OstiumVault, OstiumVersion
from eth_defi.provider.multi_provider import create_multi_provider_web3

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

STATUS_NAMES = {
    OSTIUM_REQUEST_STATUS_NONE: "NONE",
    OSTIUM_REQUEST_STATUS_PENDING: "PENDING",
    OSTIUM_REQUEST_STATUS_CLAIMABLE: "CLAIMABLE",
    OSTIUM_REQUEST_STATUS_RECLAIMABLE: "RECLAIMABLE",
}

vault_address = os.environ.get("VAULT_ADDRESS", "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")
owner_address = os.environ.get("OWNER_ADDRESS")

web3 = create_multi_provider_web3(os.environ["JSON_RPC_ARBITRUM"])
vault: OstiumVault = create_vault_instance_autodetect(web3, vault_address)
assert vault.version == OstiumVersion.v1_5, f"Expected V1.5, got {vault.version}"

contract = vault.vault_contract

last_settlement_id = contract.functions.lastSettlementId().call()
deposit_target = contract.functions.targetSettlementId(True).call()
withdraw_target = contract.functions.targetSettlementId(False).call()
last_ts = contract.functions.lastSettlementTs().call()
max_interval = contract.functions.maxSettlementInterval().call()

print(f"Vault: {vault.name} ({vault_address})")
print(f"Last settlement ID: {last_settlement_id}")
print(f"Deposit target settlement ID: {deposit_target}")
print(f"Withdraw target settlement ID: {withdraw_target}")
print(f"Last settlement timestamp: {last_ts}")
print(f"Max settlement interval: {max_interval}s ({max_interval / 3600:.1f}h)")
print()

if not owner_address:
    print("Set OWNER_ADDRESS to check deposit/withdrawal status for a specific address.")
    sys.exit(0)

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

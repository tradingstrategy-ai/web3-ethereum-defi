"""Refresh fee fields for the reviewed Tori exposure inventory.

The numbered runner owns persistence and locks. All cached identities and
live fee reads are validated before the first row is updated. Prices, token
caches, lead discovery and reader state are outside this operation.
"""

import logging

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, get_vault_protocol_name
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.erc_4626.vault_protocol.tori.disclosures import TORI_EXPOSURES
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)


def refresh_tori_risk_and_fees(database: VaultDatabase, connections: dict[int, Web3] | None = None) -> dict[str, object]:
    """Stage the current fee configuration for 17 reviewed vaults.

    Missing deployments are reported without creating rows. Present rows must
    match the reviewed chain, address, protocol, share and asset identity.
    Retain cached risk and existing notes: current risk and disclosures are
    resolved during export. Pin fee reads to one current block per chain;
    Monad uses recent state only. Provider failures leave every row unchanged.

    :param database: Shared staged metadata database owned by the runner.
    :param connections: Optional supplied chain connections for tests.
    :return: JSON-safe target, missing, changed-field and source-block report.
    :raises ValueError: If any present target or supplied connection conflicts.
    """
    targets = []
    missing = []
    for (chain_id, address), exposure in TORI_EXPOSURES.items():
        spec = VaultSpec(chain_id, address)
        row = database.rows.get(spec)
        if row is None:
            missing.append(spec.as_string_id())
            logger.warning("Reviewed Tori exposure absent: %s", spec.as_string_id())
            continue
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection):
            raise ValueError(f"Conflicting reviewed Tori exposure identity: {spec.as_string_id()}")
        row_address = row.get("Address")
        if not isinstance(row_address, str):
            raise ValueError(f"Conflicting reviewed Tori exposure identity: {spec.as_string_id()}")
        if any((detection.get_spec() != spec, row_address.lower() != address, row.get("Protocol") != exposure.protocol, get_vault_protocol_name(detection.features) != exposure.protocol, row.get("Symbol") != exposure.symbol, row.get("Denomination") != exposure.asset)):
            raise ValueError(f"Conflicting reviewed Tori exposure identity: {spec.as_string_id()}")
        targets.append((spec, row, detection))

    providers = dict(connections or {})
    blocks = {}
    for chain_id in sorted({spec.chain_id for spec, _, _ in targets}):
        if chain_id not in providers:
            providers[chain_id] = create_multi_provider_web3(read_json_rpc_url(chain_id))
        web3 = providers[chain_id]
        if web3.eth.chain_id != chain_id:
            raise ValueError(f"Tori exposure provider has wrong chain: {chain_id}")
        blocks[chain_id] = web3.eth.block_number

    updates = []
    changed_fields = {}
    for spec, row, detection in targets:
        logger.info("Reading Tori exposure fees: %s at %s", spec.as_string_id(), blocks[spec.chain_id])
        vault = create_vault_instance(providers[spec.chain_id], spec.vault_address, detection.features, default_block_identifier=blocks[spec.chain_id])
        if isinstance(vault, AccountableVault):
            fees = vault.fetch_accountable_fees(blocks[spec.chain_id]).as_generic_fee_data()
        elif type(vault) is ERC4626Vault:
            # Generic ERC-4626 has no standard fee interface. The sourced
            # disclosure records this gap; do not invoke abstract getters or
            # infer free operation from missing methods.
            fees = FeeData(None, None, None, None, None)
        else:
            fees = vault.get_fee_data(blocks[spec.chain_id])
        desired = {
            "_fees": fees,
            "Mgmt fee": fees.management,
            "Perf fee": fees.performance,
            "Deposit fee": fees.deposit,
            "Withdraw fee": fees.withdraw,
            "Protocol fee": fees.protocol,
        }
        changes = {key: value for key, value in desired.items() if key not in row or row[key] != value}
        if changes:
            changed_fields[spec.as_string_id()] = sorted(changes)
            updates.append((row, changes))
        logger.info("Tori exposure %s: %s", spec.as_string_id(), ", ".join(sorted(changes)) or "unchanged")

    for row, changes in updates:
        row.update(changes)
    return {"reviewed_targets": len(TORI_EXPOSURES), "inspected_rows": len(targets), "missing": missing, "changed_rows": len(updates), "changed_fields": changed_fields, "source_blocks": {str(chain_id): block for chain_id, block in blocks.items()}}

"""Refresh only the reviewed Veranta catalogue row through numbered migration."""

import dataclasses
import logging

from web3 import Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.veranta.constants import VERANTA_CHAIN_ID, VERANTA_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.veranta.vault import VerantaVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

logger = logging.getLogger(__name__)


def refresh_veranta_metadata(database: VaultDatabase, web3: Web3 | None = None) -> dict[str, object]:
    """Stage the existing unified vault's identity, fees, admission and capability.

    Validate cached identities and finish all reads before updating the row.
    The migration runner owns persistence, backups and locks. This helper
    neither creates vaults nor opens prices, reader state or timestamp caches.

    :param database: Runner-owned staged metadata database.
    :param web3: Optional Base connection for isolated fork tests.
    :return: JSON-safe changed-field report for the one required target.
    :raise ValueError: If the target is absent, conflicting or admission is unknown.
    """
    spec = VaultSpec(VERANTA_CHAIN_ID, VERANTA_VAULT_ADDRESS)
    row = database.rows.get(spec)
    if row is None:
        raise ValueError("Required existing Veranta vault is missing; no synthetic row created")
    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection) or detection.get_spec() != spec:
        raise ValueError("Veranta detection identity is missing or conflicting")
    allowed = {ERC4626Feature.gains_tranche_like, ERC4626Feature.veranta_like}
    if detection.features - allowed:
        raise ValueError("Veranta target has conflicting protocol features")
    if row.get("Address", spec.vault_address).lower() != spec.vault_address:
        raise ValueError("Veranta row address conflicts with target")
    if row.get("Protocol") not in {None, "ERC-4626", "<protocol not yet identified>", "Avantis", "Veranta"}:
        raise ValueError("Veranta row has conflicting protocol attribution")
    web3 = web3 if web3 is not None else create_multi_provider_web3(read_json_rpc_url(VERANTA_CHAIN_ID))
    if web3.eth.chain_id != VERANTA_CHAIN_ID:
        raise ValueError("Veranta migration requires Base")
    block = web3.eth.block_number
    features = allowed
    vault = VerantaVault(web3, spec, features=features, default_block_identifier=block)
    if vault.vault_contract.functions.asset().call(block_identifier=block).lower() != "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913":
        raise ValueError("Veranta target has unexpected denomination")
    permission = fetch_deposit_permission(vault)
    if permission is VaultDepositPermission.unknown:
        raise ValueError("Veranta admission could not be established; migration remains pending")
    fees = vault.get_fee_data(block)
    desired = {
        "Protocol": "Veranta",
        "protocol_slug": "veranta",
        "Features": ", ".join(sorted(feature.value for feature in features)),
        "features": features,
        "_detection_data": dataclasses.replace(detection, features=features),
        "Link": vault.get_link(),
        "_fees": fees,
        "Mgmt fee": fees.management,
        "Perf fee": fees.performance,
        "Deposit fee": fees.deposit,
        "Withdraw fee": fees.withdraw,
        "Protocol fee": fees.protocol,
        "_deposit_permission": permission.value,
        "_deposit_manager": vault.get_deposit_manager_capability().as_initial_public_schema(),
        "_lockup": vault.get_estimated_lock_up(),
        "_withdrawal_period": vault.get_withdrawal_period(),
    }
    staged_row = row | desired
    updates, failures, _legacy_tag_rows = collect_strategy_tag_updates(VaultDatabase(rows={spec: staged_row}))
    if failures:
        raise ValueError("Veranta strategy resolution failed")
    for update in updates:
        staged_row["_strategy_tags"] = set(update.new_tags) if update.new_tags is not None else None
    desired["_strategy_tags"] = staged_row.get("_strategy_tags")
    changed = sorted(key for key, value in desired.items() if key not in row or row[key] != value)
    row.update({key: desired[key] for key in changed})
    logger.info("Veranta %s: changed fields=%s", spec.as_string_id(), ", ".join(changed) or "none")
    return {"inspected_rows": 1, "changed_rows": int(bool(changed)), "changed_fields": changed, "read_block": block}

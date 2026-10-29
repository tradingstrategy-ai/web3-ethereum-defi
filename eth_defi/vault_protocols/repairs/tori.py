"""Refresh the one reviewed Tori staking metadata row.

The numbered migration owns storage and locks. This helper stages only
catalogue fields and never opens prices, reader state or discovery databases.
"""

import dataclasses
import logging

from web3 import Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.tori.constants import TORI_ASSET_ADDRESS, TORI_CHAIN_ID, TORI_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.tori.vault import ToriVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

logger = logging.getLogger(__name__)

#: Generic legacy attribution and the corrected native staking attribution.
ALLOWED_PROTOCOLS = frozenset({"ERC-4626", "<unknown ERC-7540>", "<protocol not yet identified>", "Tori Finance"})


def refresh_tori_metadata(database: VaultDatabase, web3: Web3 | None = None) -> dict[str, object]:
    """Stage catalogue metadata from the maintained Tori adapter.

    Validate the cached identity before reading the live cooldown. All reads
    finish before staging changes, so a failed provider read leaves the input
    untouched. An absent target is reported without creating a row. Curator
    attribution is resolved from the protocol during export.

    :param database: Shared staged database supplied by the migration runner.
    :param web3: Optional Ethereum connection for isolated integration tests.
    :return: JSON-safe inspected, missing and changed-field counts.
    :raises ValueError: If cached identity or protocol attribution conflicts.
    """
    spec = VaultSpec(TORI_CHAIN_ID, TORI_VAULT_ADDRESS)
    row = database.rows.get(spec)
    if row is None:
        logger.warning("Reviewed Tori vault absent; no row created: %s", spec.as_string_id())
        return {"inspected_rows": 0, "missing": [spec.as_string_id()], "changed_rows": 0, "changed_fields": []}

    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection) or detection.get_spec() != spec:
        message = "Tori target has missing or conflicting detection identity"
        raise ValueError(message)
    if row.get("Address", spec.vault_address).lower() != spec.vault_address:
        message = "Tori target has a conflicting row address"
        raise ValueError(message)
    if row.get("Protocol") not in ALLOWED_PROTOCOLS or get_vault_protocol_name(detection.features) not in ALLOWED_PROTOCOLS:
        message = "Tori target has a conflicting protocol"
        raise ValueError(message)
    if row.get("Denomination") != "trUSD" or row.get("Symbol") != "strUSD":
        message = "Tori target has conflicting token symbols"
        raise ValueError(message)
    denomination = row.get("_denomination_token")
    if denomination and denomination.get("address", "").lower() != TORI_ASSET_ADDRESS:
        message = "Tori target has a conflicting denomination address"
        raise ValueError(message)

    web3 = web3 if web3 is not None else create_multi_provider_web3(read_json_rpc_url(TORI_CHAIN_ID))
    if web3.eth.chain_id != TORI_CHAIN_ID:
        message = "Tori metadata refresh requires Ethereum"
        raise ValueError(message)
    block = web3.eth.block_number
    features = {ERC4626Feature.tori_like}
    vault = ToriVault(web3, spec, features=features, default_block_identifier=block)
    period = vault.get_withdrawal_period()
    fees = vault.get_fee_data(block)
    desired = {
        "Protocol": vault.get_protocol_name(),
        "protocol_slug": "tori",
        "Features": ", ".join(sorted(feature.name for feature in features)),
        "features": features,
        "_detection_data": dataclasses.replace(detection, features=features),
        "Link": vault.get_link(),
        "_description": vault.description,
        "_short_description": vault.short_description,
        "_fees": fees,
        "Mgmt fee": fees.management,
        "Perf fee": fees.performance,
        "Deposit fee": fees.deposit,
        "Withdraw fee": fees.withdraw,
        "Protocol fee": fees.protocol,
        "_lockup": period.max_period,
        "_withdrawal_period": period,
        "_deposit_manager": None,
        "_curator_slug": None,
    }
    # Resolve tags against an isolated row with the corrected detection first.
    # The shared helper canonicalises legacy tag formats and takes no locks.
    staged_row = row | desired
    tag_result = collect_strategy_tag_updates(VaultDatabase(rows={spec: staged_row}))
    if tag_result[1]:
        message = "Tori strategy-tag resolution failed"
        raise ValueError(message)
    for update in tag_result[0]:
        staged_row["_strategy_tags"] = set(update.new_tags) if update.new_tags is not None else None
    desired["_strategy_tags"] = staged_row.get("_strategy_tags")
    changed_fields = sorted(key for key, value in desired.items() if key not in row or row[key] != value)
    row.update({key: desired[key] for key in changed_fields})
    logger.info("Tori %s: %s", spec.as_string_id(), ", ".join(changed_fields) or "unchanged")
    return {"inspected_rows": 1, "missing": [], "changed_rows": int(bool(changed_fields)), "changed_fields": changed_fields}

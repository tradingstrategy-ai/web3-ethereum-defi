# ruff: noqa: N999 - Numbered filenames are required by the migration runner.
"""Onboard the four reviewed Pear vaults from standard ERC-4626 deposit leads.

Purpose: Refresh Pear protocol identity, descriptions, strategy tags and fees.
Scope: Four reviewed HyperEVM vaults and their existing discovery leads.
Branch: examine-pear-garden-vault-protocol
PR title: feat: add Pear vault support on HyperEVM
PR: See the pull request for the branch above.
Preserves: Other vaults, existing leads, scan cursor, price files and reader state.
Expected: Reclassify each saved lead or report it as missing for normal discovery.

The configured HyperEVM RPCs could not value Pear at explicit historical blocks.
The live scanner uses the ERC-4626 reader at the current head. Migration 098
imports earlier snapshots from Pear's API. This metadata migration
does not attempt a backfill against unreadable HyperCore precompile state.

LLM-generated operational migration; changes require human review.
"""

from dataclasses import replace

from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext

#: Funded deployments verified against standard Deposit logs.
TARGETS = (
    VaultSpec(PEAR_CHAIN_ID, "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096"),
    VaultSpec(PEAR_CHAIN_ID, "0xd38a67a4f54a9b4286d72f08440485a395ddfa70"),
    VaultSpec(PEAR_CHAIN_ID, "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84"),
    VaultSpec(PEAR_CHAIN_ID, "0x69f2ed780a4cec49e552dbcd35a60f41368bc362"),
)


def migrate(context: MigrationContext) -> None:
    """Rebuild reviewed rows with the same adapter as the live scanner.

    Use saved event data for discovery dates and counts. Validate all rows
    before staging changes so a partial RPC failure cannot publish a repair.

    :param context: Guarded migration context owning backups and metadata writes.
    :return: None after preview or commit.
    """
    database = context.metadata()
    missing_specs = []
    staged_rows = {}
    web3 = None
    now = native_datetime_utc_now()
    for spec in TARGETS:
        old_row = database.rows.get(spec, {})
        detection = old_row.get("_detection_data")
        lead = database.leads.get(spec)
        if lead is None and detection is None:
            missing_specs.append(spec.as_string_id())
            continue
        address = Web3.to_checksum_address(spec.vault_address)
        if web3 is None:
            web3 = create_multi_provider_web3(read_json_rpc_url(PEAR_CHAIN_ID))
        features = detect_vault_features(web3, address, verbose=False)
        if features != {ERC4626Feature.pear_like}:
            raise ValueError(f"Unable to verify Pear deployment {spec}")
        if lead is not None:
            detection = ERC4262VaultDetection(PEAR_CHAIN_ID, address, lead.first_seen_at_block, lead.first_seen_at, features, now, lead.deposit_count, lead.withdrawal_count)
        else:
            if detection.get_spec() != spec:
                raise ValueError(f"Conflicting Pear discovery identity for {spec}")
            detection = replace(detection, features=features, updated_at=now)

        row = create_vault_scan_record(web3, detection, "latest", token_cache={})
        if row.get("Protocol") != "Pear" or row.get("_rpc_failure_category") is not None or row.get("NAV") is None:
            raise ValueError(f"Unable to rebuild Pear metadata for {spec}")
        if row.get("_notes") is None and "_notes" in old_row:
            row["_notes"] = old_row["_notes"]
        staged_rows[spec] = old_row | row | {"protocol_slug": "pear"}

    database.rows.update(staged_rows)
    context.report(refreshed_rows=len(staged_rows), missing_specs=missing_specs)
    context.commit_metadata()

"""Correct the Birch Hill Flagship vault's persisted admission policy.

Purpose: Replace a false permissionless result caused by the absent canonical
Yearn deposit-limit selector with the verified hook-based admission read.
Scope: Existing Base RWA USDC row only; _deposit_permission and _whitelist_notes.
Branch: analyse-birch-hill-vault-protocol
PR title: feat: publish Birch Hill vault descriptions and correct deposit permissions
PR: Not opened; add the PR URL before merge and production execution.
Preserves: Other metadata, prices, leads, reader state and timestamp caches.
Expected: At most one changed row and two changed fields; missing row reported.

This accessor-only repair does not discover vaults or alter price history, so
no price backfill is required. LLM-generated migration; requires human review.
Both preview and apply require stopped scanner and competing writers. Inspect
production docker-compose.yml and build the merged image before maintenance.
The runner owns the lifetime guard before the writer lock and all persistence.

Run after merge from ~/vault-scanner/web3-ethereum-defi::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
    docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt

Review the full pending batch preview. Normal scanner exports/uploads must
publish the corrected admission policy after apply; migration alone does not.
Build the merged checkout with scripts/build-container.sh to stamp its revision.
Use that image for maintenance and restart; compare its stamped commit with
migration.json rather than relying on a mutable image tag.
"""

# ruff: noqa: N999

import logging

from eth_defi.erc_4626.scan import fetch_deposit_permission
from eth_defi.erc_4626.vault_protocol.yearn.vault import BIRCH_HILL_RWA_USDC, YearnV3Vault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault_protocols.migration import MigrationContext

logger = logging.getLogger(__name__)


def migrate(context: MigrationContext) -> None:
    """Refresh only the existing Flagship row through the maintained adapter.

    Resolve policy before staging changes. Missing discovery is reported
    without inventing a metadata row; unreadable policy aborts the migration.

    :param context: Guarded migration context with staged metadata.
    :return: ``None`` after reporting and committing or previewing changes.
    :raises ValueError: If RPC chain or stored identity conflicts, or policy is unknown.
    """
    database = context.metadata()
    spec = BIRCH_HILL_RWA_USDC
    row = database.rows.get(spec)
    if row is None:
        context.report(inspected_rows=0, changed_rows=0, updated_fields=0, missing=[spec.as_string_id()])
        context.commit_metadata()
        return
    if row.get("Address", spec.vault_address).lower() != spec.vault_address:
        raise ValueError(f"Conflicting Birch Hill vault identity: {spec}")
    if row.get("Protocol") not in {"Yearn", "ERC-4626"}:
        raise ValueError(f"Unexpected protocol for Birch Hill Flagship: {row.get('Protocol')}")
    logger.info("Reading Birch Hill Flagship deposit hook on Base")
    web3 = create_multi_provider_web3(read_json_rpc_url(spec.chain_id))
    if web3.eth.chain_id != spec.chain_id:
        message = "Birch Hill repair requires Base RPC"
        raise ValueError(message)
    vault = YearnV3Vault(web3, spec, default_block_identifier=web3.eth.block_number)
    permission = fetch_deposit_permission(vault)
    if permission is VaultDepositPermission.unknown:
        message = "Birch Hill deposit hook is unresolved; repair remains pending"
        raise ValueError(message)
    updates = {"_deposit_permission": permission.value, "_whitelist_notes": vault.get_whitelist_notes()}
    changed = {key: value for key, value in updates.items() if row.get(key) != value}
    row.update(changed)
    context.report(inspected_rows=1, changed_rows=int(bool(changed)), updated_fields=len(changed), deposit_permission=permission.value)
    context.commit_metadata()

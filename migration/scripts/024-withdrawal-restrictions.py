"""Persist descriptive withdrawal warnings for the reviewed vaults and D2.

Purpose: Publish sourced exit restrictions without hiding vaults or losing
existing flags and strategy notes.
Scope: Three exact deployments below and every metadata row whose Protocol
is D2 Finance, including newly discovered D2 deployments; only _flags/_notes.
Branch: add-plutus-closed-withdrawal-note-review-d2
PR title: notes: explain restricted withdrawals for Plutus, D2 and reviewed vaults
PR: See the pull request for branch add-plutus-closed-withdrawal-note-review-d2.
Preserves: 40acres flags and notes, unrelated metadata, prices, leads, reader
state and timestamp caches. 40acres liquidity/repayment risk remains separate.
Expected: Three validated targets plus all stored D2 rows; safe to retry.
No historical price backfill is needed.

LLM-generated operational migration; changes require human review.

Run after merge
--------------
Use ~/vault-scanner/web3-ethereum-defi and inspect docker-compose.yml first.
Both preview and apply require the looped scanner to be stopped. The runner
acquires the lifetime guard before the writer lock. Quiesce other writers too.

.. code-block:: shell

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
    docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt

The runner executes all pending migrations; review the combined preview and
check that the stamped image commit matches migration.json. After normal
publication, verify exported flags and notes for all reviewed deployments and
D2, preserving strategy links and existing warnings. This migration does not
itself publish data or add a frontend badge.
"""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext

#: Exact chain/address inventory and expected persisted protocol identities.
TARGETS = (
    (VaultSpec(42161, "0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a"), "Plutus"),
    (VaultSpec(1868, "0x2ba3fc5bdbbe7be2630fb71e759739625246bcd9"), "Asseto"),
    (VaultSpec(1, "0xb250c9e0f7be4cff13f94374c993ac445a1385fe"), "ETH Strategy"),
)


def migrate(context: MigrationContext) -> None:
    """Refresh exit warnings after validating the complete named inventory.

    D2's adapter generates strategy-specific notes without RPC calls and keeps
    manual overrides authoritative. The context owns staging and persistence;
    retain unrelated flags, metadata and historical files in either run mode.

    :param context: Guarded migration context with the staged metadata store.
    :return: ``None`` after reporting changes and committing staged metadata.
    :raises ValueError: If a named deployment is missing or misidentified.
    """
    database = context.metadata()
    for spec, protocol in TARGETS:
        if spec not in database.rows:
            raise ValueError(f"Reviewed withdrawal-warning vault row is missing: {spec}")
        if database.rows[spec].get("Protocol") != protocol:
            raise ValueError(f"Unexpected protocol for reviewed withdrawal-warning vault: {spec}")

    targets = dict(TARGETS)
    targets.update({spec: "D2 Finance" for spec, row in database.rows.items() if row.get("Protocol") == "D2 Finance"})
    # Compute every proposal before modifying the staged rows.
    updates = {}
    for spec, protocol in targets.items():
        if protocol == "D2 Finance":
            note = D2Vault(web3=None, spec=spec, features={ERC4626Feature.d2_like}).get_notes()
        else:
            note = get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=protocol)
        updates[spec] = {
            "_flags": set(database.rows[spec].get("_flags") or ()) | get_vault_special_flags(spec.vault_address, protocol),
            "_notes": note,
        }

    changed_rows = 0
    updated_fields = 0
    for spec, fields in updates.items():
        row = database.rows[spec]
        changed_fields = {key: value for key, value in fields.items() if row.get(key) != value}
        changed_rows += bool(changed_fields)
        updated_fields += len(changed_fields)
        row.update(changed_fields)

    context.report(inspected_rows=len(targets), changed_rows=changed_rows, updated_fields=updated_fields)
    context.commit_metadata()

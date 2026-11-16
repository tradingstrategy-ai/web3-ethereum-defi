"""Persist the reviewed valuation exclusions and investigation flags.

Purpose: Stop publishing unsupported canonical-asset valuations as investable
and retain review warnings where lending-claim recoverability is uncertain.
Scope: Eleven exact chain/address/underlying pairs below; only _flags/_notes.
The reviewed manual notes replace the targets' cached notes.
Branch: check-vault-blacklist-flags-fake-underlying
PR title: notes: flag unsupported vault valuations and pending investigations
PR: See the pull request for the branch above.
Preserves: Unrelated flags and metadata, prices, leads, reader state and caches.
Expected: Eleven validated targets; at most 22 changed fields; safe to retry.
No price backfill is needed: this repair changes warnings, not price discovery.

LLM-generated operational migration; changes require human review.

Run after merge
---------------
Inspect docker-compose.yml in ~/vault-scanner/web3-ethereum-defi and build the
merged checkout with scripts/build-container.sh to stamp its git revision.
Quiesce other one-shot/cron writers as well as the services below. Both preview
and apply require the looped scanner stopped. The runner owns the lifecycle
guard, acquired before its writer lock, backups and atomic persistence.

.. code-block:: shell

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
    docker compose exec vault-scanner-looped cat /usr/src/web3-ethereum-defi/GIT_VERSION_HASH.txt

Review all pending migrations in preview. Confirm the printed commit matches
migration.json. The runner changes vault-metadata-db.pickle and its journal and
backups; it does not publish. After normal scanner publication, verify the nine
misleading_valuation records have risk Blacklisted and the two review_needed
records retain visibility, unless another exclusion independently applies.
Public notes were checked against the description writing style guide.
"""

from eth_typing import HexAddress

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import get_notes, get_vault_special_flags
from eth_defi.vault_protocols.migration import MigrationContext

#: Fixed reviewed inventory, including the cached denomination-token identity.
TARGETS = (
    (VaultSpec(1, "0xe86fb0d3cc16c4f9d97d734795c4f43da6fd3751"), HexAddress("0x6cf92f7752f97350417548c70333b8e0acb7d8e2")),
    (VaultSpec(56, "0x1acbe95a3478ca508b356da1f9918678c254f88f"), HexAddress("0xdf03387a1995af4a0fea74f52159325c8f9e9ecc")),
    (VaultSpec(56, "0x6316771916aa933189aa4e43290d0f69ad6909eb"), HexAddress("0xe8a5cb0a74bdf4bbaeb3955d0b829134e6437862")),
    (VaultSpec(56, "0x3c770ab0c25c77b44b4d76af1b8067fbbc16da5d"), HexAddress("0xdf03387a1995af4a0fea74f52159325c8f9e9ecc")),
    (VaultSpec(56, "0x620270019370766bc55099299e38c2504a984211"), HexAddress("0xe8a5cb0a74bdf4bbaeb3955d0b829134e6437862")),
    (VaultSpec(56, "0xb7dc5eefa04685f326883b3c5067742102beaca3"), HexAddress("0xe8a5cb0a74bdf4bbaeb3955d0b829134e6437862")),
    (VaultSpec(56, "0xd308adec40c09b7ebceeb665e8799feb8f6728d9"), HexAddress("0xdf03387a1995af4a0fea74f52159325c8f9e9ecc")),
    (VaultSpec(143, "0xc5a195b4be1cc20e6f5390a0c09512124e7eb23a"), HexAddress("0x1b5c0bebae484759c757038b2fe39adc7f4d9af9")),
    (VaultSpec(4663, "0x2f060715ef9de33236653bb5e73c62e0cac7d585"), HexAddress("0xc6d0a987c6839d08516bcd27ad89b3bcc8bbaac1")),
    (VaultSpec(137, "0x182b06a514c29d07384838883efc02c8f0431b42"), HexAddress("0x2791bca1f2de4661ed88a30c99a7a9449aa84174")),
    (VaultSpec(43114, "0x7437ac81457fa98ffb2d0c8f9943ecfe4813e2f1"), HexAddress("0x152b9d0fdc40c096757f570a51e494bd4b943e50")),
)


def migrate(context: MigrationContext) -> None:
    """Merge reviewed warnings after validating every underlying identity.

    Preserve existing flags and avoid network reads. Validation completes
    before any staged row changes, so a conflicting deployment fails cleanly.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after reporting and committing the proposed changes.
    :raises ValueError: If a reviewed row is absent or its underlying differs.
    """
    database = context.metadata()
    for spec, asset in TARGETS:
        if spec not in database.rows:
            raise ValueError(f"Reviewed valuation vault row is missing: {spec}")
        token = database.rows[spec].get("_denomination_token")
        cached_address = token.get("address") if isinstance(token, dict) else None
        if not isinstance(cached_address, str) or cached_address.lower() != asset:
            raise ValueError(f"Unexpected underlying for reviewed valuation vault: {spec}")

    changed_rows = 0
    updated_fields = 0
    for spec, _ in TARGETS:
        row = database.rows[spec]
        protocol = row.get("Protocol")
        updates = {
            "_flags": set(row.get("_flags") or ()) | get_vault_special_flags(spec.vault_address, protocol),
            "_notes": get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=protocol),
        }
        changed_fields = {key: value for key, value in updates.items() if row.get(key) != value}
        row.update(changed_fields)
        changed_rows += bool(changed_fields)
        updated_fields += len(changed_fields)

    context.report(inspected_rows=len(TARGETS), changed_rows=changed_rows, updated_fields=updated_fields)
    context.commit_metadata()

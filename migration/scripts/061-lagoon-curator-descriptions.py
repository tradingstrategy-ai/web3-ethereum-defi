# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Persist descriptions for the reviewed Lagoon curator vault inventory.

Purpose: Publish handwritten summaries and full descriptions, only where substantive
    source material supports an individual vault description.
Scope: 25 frozen deployments below; _description and _short_description only.
Branch: add-lagoon-curator-records
PR title: feat: add Lagoon curator records and reviewed vault descriptions
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1757
Preserves: Names, curator attribution, tags, notes, fees, flags, eligibility,
    unrelated rows, prices, leads, reader state and timestamp caches.
Expected: At most 25 existing rows and 50 changed fields; missing rows reported.

LLM-generated operational migration; changes require human review. No network
reads, discovery or historical price scanning. Existing price gaps are documented
separately in docs/protocol-research/lagoon-curator-onboarding-2026-11-10.md.
This copy refresh does not newly admit a vault or extend its historical coverage.

Run after merge: inspect docker-compose.yml and persistent mounts, build the
merged stamped image and stop all competing writers before preview and apply.
From ~/vault-scanner/web3-ethereum-defi::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner

Review the entire pending batch and its counts before apply. Use the same image
and persistent mounts throughout. The runner acquires the lifetime guard before
the writer lock. Normal scanner export and upload publish the repaired copy.
"""

from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed deployments; keep migration scope independent of future catalogue additions.
TARGETS = {
    VaultSpec(1, "0x2746f31096f23670caf4043f8b30d8d02405a257"): {"Lagoon Finance"},
    VaultSpec(1, "0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8"): {"Lagoon Finance"},
    VaultSpec(1, "0x4cb280e63251b9ab24a54def74bf5995d82ff398"): {"Lagoon Finance"},
    VaultSpec(1, "0x59b7942f7d2afd085691ce65c152e0d38d4eff22"): {"Lagoon Finance"},
    VaultSpec(1, "0x7d2c2f54792ad72cb834d298f542145b06b703cb"): {"Lagoon Finance"},
    VaultSpec(1, "0x9a458370556a68bdc130a80c35b294d0ad947a6a"): {"Lagoon Finance"},
    VaultSpec(1, "0xca790385506b790554571cbc9da73f0130cdcfd5"): {"Lagoon Finance"},
    VaultSpec(1, "0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89"): {"Lagoon Finance"},
    VaultSpec(143, "0xe95074a86fead8647edff36af2f3f1dd7e8f7adb"): {"Lagoon Finance"},
    VaultSpec(8453, "0x0a63471da694fc822f5a0a0b65978dda8c8edbe5"): {"Lagoon Finance"},
    VaultSpec(8453, "0x4efc07dca8697792119484af33549f33ab11bf3c"): {"Lagoon Finance"},
    VaultSpec(8453, "0x8092ca384d44260ea4feaf7457b629b8dc6f88f0"): {"Lagoon Finance"},
    VaultSpec(8453, "0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523"): {"Lagoon Finance"},
    VaultSpec(8453, "0xb09f761cb13baca8ec087ac476647361b6314f98"): {"Lagoon Finance"},
    VaultSpec(8453, "0xb725f2277f3f62ad017a6dc418433b326a9f6334"): {"Lagoon Finance"},
    VaultSpec(8453, "0xbe7db44f4ce20dac83b578b94fd35087f66e9754"): {"Lagoon Finance"},
    VaultSpec(8453, "0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550"): {"Lagoon Finance"},
    VaultSpec(8453, "0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4"): {"Lagoon Finance"},
    VaultSpec(8453, "0xf56bfe07b8d6e6d74258cdb6969a633629b06b08"): {"Lagoon Finance"},
    VaultSpec(42161, "0x9e6de1f581d63129af453fe1ea7253b280b4dca8"): {"Lagoon Finance"},
    VaultSpec(1, "0x845339546d90e6e21fdb9638cc7d8d15ebb5e4ac"): {"Morpho"},
    VaultSpec(1, "0x29ca54dd3ae9e43abb10880f5e08da439bf25f99"): {"Morpho"},
    VaultSpec(8453, "0xcda5dbf58a016158ecd4a226109752cc3a627ec8"): {"Morpho"},
    VaultSpec(143, "0x9f209b43559b13b783eaa0a6686921c7b174f269"): {"Morpho"},
    VaultSpec(999, "0x3f78bdd48b363ec927dc227d6c80a6e4821c7e10"): {"Morpho"},
}


def migrate(context: MigrationContext) -> None:
    """Stage the adapter descriptions after validating every existing target.

    Missing rows are reported without inserting incomplete vault records.
    The runner owns preview staging, atomic writes and backups.

    :param context: Guarded migration context and staged metadata.
    :return: None after reporting and committing the proposed descriptions.
    :raises ValueError: If a target has conflicting protocol attribution.
    """
    database = context.metadata()
    for spec, protocols in TARGETS.items():
        row = database.rows.get(spec)
        if row is not None and row.get("Protocol") not in protocols:
            raise ValueError(f"Unexpected protocol for curator description target {spec}: {row.get('Protocol')}")
    inspected = changed_rows = updated_fields = 0
    for spec in TARGETS:
        row = database.rows.get(spec)
        if row is None:
            continue
        metadata = get_handwritten_vault_metadata(spec.chain_id, spec.vault_address)
        assert metadata is not None
        fields = {"_description": normalise_vault_description(metadata.description), "_short_description": normalise_vault_short_description(metadata.short_description)}
        updates = {key: value for key, value in fields.items() if row.get(key) != value}
        row.update(updates)
        inspected += 1
        changed_rows += bool(updates)
        updated_fields += len(updates)
    context.report(inspected_rows=inspected, changed_rows=changed_rows, updated_fields=updated_fields, missing_specs=[spec.as_string_id() for spec in TARGETS if spec not in database.rows])
    context.commit_metadata()

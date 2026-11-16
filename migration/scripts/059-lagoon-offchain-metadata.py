# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Fill reviewed Lagoon descriptions omitted by the public catalogue cache.

Purpose: Repair persisted descriptions for recognised but unlisted vaults.
Scope: Twenty-two frozen deployments; descriptions only, fill-only.
Branch: check-lagoon-vault-descriptions-strategy-tags
PR title: fix: refresh Lagoon metadata and strategy tags
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1756
Preserves: Flags, curator assignments, fees, notes, prices, leads, reader state,
    timestamp caches and all non-description metadata. No rows are created.
Expected: At most 22 existing rows and 43 fields (21 pairs and Strada summary).

LLM-generated operational migration; changes require human review.
Run after merge with a new stamped image, following
.claude/docs/migration-scripts.md. Inspect docker-compose.yml and mounted state,
stop vault-scanner-looped and competing writers before BOTH preview and apply.
Use the same inspected image/mounts for vault-scanner-oneshot with entrypoint
vault-migrate: DRY_RUN=true run, then DRY_RUN=false run after reviewing counts.
Restart that image, then normal cleaning/export/upload publishes the changes.
The runner takes lifetime guard before writer lock; this script takes neither.
No price backfill: descriptive text only, no eligibility changes.

Provider reads use temporary caches in preview and apply. A 429 aborts the
attempt: wait for its reported Retry-After deadline before a new preview.
Never retry an unchanged burst. No successful attempt may contain partial
provider results. Missing deployments are reported without creating rows.
"""

import time
from pathlib import Path
from tempfile import TemporaryDirectory

from tqdm_loggable.auto import tqdm

from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import fetch_lagoon_metadata_snapshot
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault_protocols.migration import MigrationContext

#: Reviewed 2026-11-10 source inventory. Boolean means a long field is required.
#: Evidence: PR 1756 research comment and each exact-address Lagoon detail API.
TARGETS: tuple[tuple[int, str, bool], ...] = (
    #: Gami D1.
    (1, "0x16b85563902556d205f5c2b4485df3d26b153f96", True),
    #: Gami Stake DAO USDC.
    (1, "0x33e1339567c183fbadcb43f72d11c47229d468ab", True),
    #: Gami Capital lvlUSD.
    (1, "0x59b7942f7d2afd085691ce65c152e0d38d4eff22", True),
    #: Autonomous Liquidity Plus.
    (1, "0x5a97b0b97197299456af841f8605543b13b12ee3", True),
    #: BOLD friendly forks Earn.
    (1, "0x68c2243619760abe0982378197c8fef2f651bd31", True),
    #: Noon tacUSN.
    (1, "0x7895a046b26cc07272b022a0c9bafc046e6f6396", True),
    #: Flint USD.
    (1, "0x7f35dea44a192764aa50d50e5f0ece1d5a8b0e45", True),
    #: Hub Capital crvUSD Vault.
    (1, "0x9a458370556a68bdc130a80c35b294d0ad947a6a", True),
    #: Strada USDC.
    (1, "0xcb58582b0d52ce5feecb06ba9ce66598b0d57886", False),
    #: MuDigital Tulipa USDC.
    (143, "0x0da39b740834090c146dc48357f6a435a1bb33b3", True),
    #: TID Capital Arbitrum Yield.
    (42161, "0x647b5ad322c9173a8d3bdf8041b8bddfc456ddc4", True),
    #: Nutria.
    (42161, "0x7c43710b240d1810d9799001ec0964512ffdb077", True),
    #: DeTrade Applefarm USDC.
    (8453, "0x0a63471da694fc822f5a0a0b65978dda8c8edbe5", True),
    #: MoneyFi FlowForge Vault.
    (8453, "0x4efc07dca8697792119484af33549f33ab11bf3c", True),
    #: BTC DCA - Regime Vault.
    (8453, "0x87b433543660001cf64ad50afc51cc2a27e73e1d", True),
    #: 722Capital-USDC.
    (8453, "0xb09f761cb13baca8ec087ac476647361b6314f98", True),
    #: MSA - Portefeuille Défensif.
    (8453, "0xb725f2277f3f62ad017a6dc418433b326a9f6334", True),
    #: TruMarket.
    (8453, "0xbe7db44f4ce20dac83b578b94fd35087f66e9754", True),
    #: GUSDC.
    (8453, "0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4", True),
    #: MSA - Portefeuille Dynamique.
    (8453, "0xf56bfe07b8d6e6d74258cdb6969a633629b06b08", True),
    #: TID Capital Plasma Yield.
    (9745, "0x0c1c83c9c203f6920726a0045ff1ea95e9698d0a", True),
    #: Cassa Yen Carry & Divergence Trade.
    (9745, "0xff5ef6464896516c6ccf60da9e199680929fb01c", True),
)


def migrate(context: MigrationContext) -> None:
    """Validate a paced exact-address batch before staging any metadata writes.

    Reuses the scanner parser and normalisation. Existing non-empty copy wins,
    and Strada's known-missing long description is outside its required scope.

    :param context: Runner-owned staged database, reports and atomic commit.
    :return: None after a complete preview or apply.
    :raises ValueError: For conflicting protocol attribution.
    :raises RuntimeError: If any required provider field cannot be verified.
    """
    database = context.metadata()
    missing = []
    pending = []
    for chain_id, address, require_long in TARGETS:
        spec = VaultSpec(chain_id, address)
        row = database.rows.get(spec)
        if row is None:
            missing.append(spec.as_string_id())
            continue
        if row.get("Protocol") != "Lagoon Finance":
            raise ValueError(f"Lagoon description target {spec} has conflicting protocol {row.get('Protocol')!r}")
        fields = ("_short_description", "_description") if require_long else ("_short_description",)
        if any(not (row.get(field) or "").strip() for field in fields):
            pending.append((spec, row, fields))

    updates = []
    conflicts = []
    previous_request = 0.0
    with TemporaryDirectory(prefix="lagoon-description-migration-") as temporary:
        for spec, row, fields in tqdm(pending, desc="Checking Lagoon descriptions"):
            delay = max(0.0, previous_request + 1.0 - time.monotonic())
            if delay:
                time.sleep(delay)
            previous_request = time.monotonic()
            snapshot = fetch_lagoon_metadata_snapshot(spec.chain_id, spec.vault_address, cache_path=Path(temporary), refresh_listing=False)
            if snapshot.outcome != "success":
                # A failed batch is never recorded as successfully repaired.
                retry_at = snapshot.retry_at
                raise RuntimeError(f"Lagoon description batch deferred for {spec}: {snapshot.outcome}; earliest new preview {retry_at} UTC")
            metadata = snapshot.metadata
            values = {"_description": normalise_vault_description(metadata.get("description")), "_short_description": normalise_vault_short_description(metadata.get("short_description"))}
            for field in fields:
                if not values[field]:
                    raise RuntimeError(f"Lagoon description source changed for {spec}: required field {field} is empty; review the frozen inventory")
                if not (row.get(field) or "").strip():
                    updates.append((spec, field, values[field]))
                elif row[field] != values[field]:
                    conflicts.append({"vault": spec.as_string_id(), "field": field})

    for spec, field, value in updates:
        database.rows[spec][field] = value
    context.report(inspected_rows=len(TARGETS), missing_specs=missing, changed_rows=len({spec for spec, _, _ in updates}), updated_fields=len(updates), preserved_conflicts=conflicts)
    context.commit_metadata()

"""Refresh persisted strategy tags for reviewed Lighter public pools.

Purpose: Publish the portfolio and trading classifications from the November review.
Scope: Only _strategy_tags for the 42 listed Lighter Core pools on synthetic chain 9998.
Branch: lighter-vaults-strategy-tagging-analysis
PR title: feat: classify Lighter public pool strategies
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1795
Preserves: All other metadata, prices, leads, reader state and timestamp caches.
Expected: Up to 42 updated fields. Report missing deployments without creating rows.
The tag refresh requires no network calls or historical price backfill.

LLM-generated operational migration; changes require human review.
Run after merge: stop competing writers and vault-scanner-looped before preview
and apply, following .claude/docs/migration-scripts.md. From the production checkout:
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
Then use the same command with DRY_RUN=false. Resume normal exports afterwards.
"""

# ruff: noqa: N999 - Numbered migration filenames are required by the runner.

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.lighter.constants import LIGHTER_ETHEREUM
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.strategy_tags import collect_strategy_tag_updates

#: Core account indices whose maintained tags changed in this review.
ACCOUNT_INDICES = (
    281474976415828,
    281474976466894,
    281474976486477,
    281474976488848,
    281474976488851,
    281474976488853,
    281474976489165,
    281474976489166,
    281474976490589,
    281474976492148,
    281474976498676,
    281474976501540,
    281474976505621,
    281474976543116,
    281474976622846,
    281474976623750,
    281474976624925,
    281474976625478,
    281474976626327,
    281474976626973,
    281474976637751,
    281474976638939,
    281474976639900,
    281474976643102,
    281474976643789,
    281474976643895,
    281474976644089,
    281474976644348,
    281474976644793,
    281474976651007,
    281474976655272,
    281474976656541,
    281474976656586,
    281474976656876,
    281474976666704,
    281474976666853,
    281474976680236,
    281474976684763,
    281474976687595,
    281474976688087,
    281474976688779,
    281474976694250,
)


def migrate(context: MigrationContext) -> None:
    """Stage strategy tags for the reviewed Lighter Core pools.

    Resolve tags through the same registry used by recurring exports. Scope the
    refresh by synthetic address, which distinguishes Core from Robinhood.
    The migration runner controls preview and persistence.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after staging and reporting the refresh.
    :raises ValueError: If a present target lacks Lighter native classification
        or resolvable strategy tags.
    """
    database = context.metadata()
    targets = tuple(VaultSpec(LIGHTER_ETHEREUM.chain_id, LIGHTER_ETHEREUM.format_pool_address(index)) for index in ACCOUNT_INDICES)
    target_rows = {spec: database.rows[spec] for spec in targets if spec in database.rows}
    for spec, row in target_rows.items():
        detection = row.get("_detection_data")
        if row.get("Protocol") != "Lighter" or not isinstance(detection, ERC4262VaultDetection) or ERC4626Feature.lighter_native not in detection.features:
            raise ValueError(f"Target {spec.as_string_id()} is not classified as Lighter native")

    updates, skipped, invalid = collect_strategy_tag_updates(VaultDatabase(rows=target_rows))
    if skipped or any(update.new_tags is None for update in updates):
        raise ValueError("Could not resolve strategy tags for all reviewed Lighter pools")

    for update in updates:
        database.rows[update.spec]["_strategy_tags"] = set(update.new_tags)

    context.report(
        inspected_rows=len(target_rows),
        missing_specs=[spec.as_string_id() for spec in targets if spec not in target_rows],
        updated_fields=len(updates),
        legacy_tag_rows=invalid,
    )
    context.commit_metadata()

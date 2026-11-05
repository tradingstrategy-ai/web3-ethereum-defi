# ruff: noqa: N999 - Numbered migration filenames are required by the runner.
"""Refresh D2 Finance notes after the writing-style audit.

Purpose: Remove repetitive introductions, research dates and unexplained jargon; date the historical Texas Hedge setup.
Scope: Only _notes on D2 Finance rows containing empty or exactly recognised legacy adapter notes.
Branch: notes/d2-globalmacro-and-note-style
PR title: notes: describe GlobalMacro and simplify D2 vault notes
PR: See the pull request for branch notes/d2-globalmacro-and-note-style.
Preserves: Descriptions, summaries, manual/custom notes, flags, fees, tags, links, unrelated metadata, leads, prices, reader state and timestamp caches.
Expected: One field per eligible existing D2 row; manual/custom notes reported and preserved. Idempotent transformation.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific input. No network reads or price backfill are needed.

Production reminder: after merge, inspect docker-compose.yml and build the stamped
image with scripts/build-container.sh. From ~/vault-scanner/web3-ethereum-defi,
stop persistent writers and quiesce other one-shot/cron writers before preview or apply::

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner

Review the preview before applying all pending migrations, including 034.
Use the same inspected image and persistent mounts throughout maintenance. The
runner takes the lifetime guard before the writer lock. Confirm the restarted
image's GIT_VERSION_HASH.txt matches migration.json; normal scanner exports and
uploads publish the notes. See .claude/docs/migration-scripts.md.
"""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.vault import D2_PROTOCOL_NAME, D2_TEXAS_HEDGE_ADDRESS, D2_TEXAS_HEDGE_BLOG_URL, D2_VAULT_ARTICLE_URLS, D2Vault, format_d2_strategy_url
from eth_defi.vault.base import normalise_vault_description
from eth_defi.vault.flag import get_notes
from eth_defi.vault_protocols.migration import MigrationContext

# Frozen pre-audit text recognises only known adapter output, including the
# older variant without the withdrawal paragraph. Never overwrite appended
# incident warnings or custom stored notes based on a broad prefix match.
LEGACY_WITHDRAWAL = "#### Withdrawal restrictions\n\n**Deposits and withdrawals are closed during the trading phase.** D2's [vault design documentation](https://docs.d2.finance/docs/vault-design), reviewed on 2 November 2026, explains that each trading cycle, called an epoch, has separate funding, trading and withdrawal phases. During trading, the trader may take custody of the vault's funds.\n\nWithdrawals reopen after the trader returns the funds and the epoch ends. A displayed epoch-end time is an estimate, not a guaranteed withdrawal deadline: the funds must also be back in the vault before withdrawals can proceed."
LEGACY_GENERIC = "D2 Finance strategy vault.\n\n**Summary:** This vault is a D2 Finance managed strategy vault. D2 strategies are tokenised derivative strategies executed onchain with proprietary offchain trading logic and epoch-based funding, trading and withdrawal phases.\n\nThe authoritative D2 source for this vault is the [D2 strategy page]({strategy_url}).{article_note}\n\n{withdrawal_note}\n"
LEGACY_TEXAS = "D2 Finance texasHedge (TXHEDGE) is a HyperEVM USDC tactical strategy vault.\n\n**Summary:** The vault is a high-risk, multi-leg directional strategy around SpaceX pre-IPO exposure and related market flows. It combines a financed SpaceX perpetual position with long S&P downside convexity and long Tesla/Mag-7 upside convexity, funded by selling DRAM upside convexity. D2 describes the strategy in its [Texas Hedge Epoch 2 blog post]({blog_url}).\n\nThe authoritative D2 source for this vault is the [D2 strategy page]({strategy_url}).\n\nThis is not a market-neutral yield product; D2 categorises it as a degen/tactical strategy where investors can lose the full vault stake.\n\n{withdrawal_note}\n"


def legacy_notes(address: str) -> set[str]:
    """Render the recognised historical note variants for one address.

    The frozen templates protect custom notes from an indiscriminate refresh.
    Source links use the existing D2 article inventory and native URL helper.

    :param address: Lowercase deployment address from the metadata key.
    :return: Normalised legacy notes, with and without the exit warning.
    """
    article_url = D2_VAULT_ARTICLE_URLS.get(address)
    article_note = f"\n\nD2 also describes this strategy in its [D2 strategy article]({article_url})." if article_url else ""
    template = LEGACY_TEXAS if address == D2_TEXAS_HEDGE_ADDRESS else LEGACY_GENERIC
    return {normalise_vault_description(legacy_template.format(strategy_url=format_d2_strategy_url(address), article_note=article_note, blog_url=D2_TEXAS_HEDGE_BLOG_URL, withdrawal_note=withdrawal)) for legacy_template in (template, template.replace("executed onchain with proprietary offchain trading logic", "executed on-chain with proprietary off-chain trading logic")) for withdrawal in ("", LEGACY_WITHDRAWAL)}


def migrate(context: MigrationContext) -> None:
    """Refresh recognised adapter notes while preserving manual warnings.

    Restrict the transformation to persisted D2 protocol rows. Skip current
    manual overrides and unknown stored text, reporting them for review; the
    runner owns locking, backups, preview and persistence.

    :param context: Guarded migration context and staged metadata database.
    :return: ``None`` after reporting and committing or previewing notes.
    """
    database = context.metadata()
    updates = {}
    preserved = []
    inspected = 0
    for spec, row in database.rows.items():
        if row.get("Protocol") != D2_PROTOCOL_NAME:
            continue
        inspected += 1
        if get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=D2_PROTOCOL_NAME, include_defaults=False):
            preserved.append(spec.as_string_id())
            continue
        note = D2Vault(None, spec, features={ERC4626Feature.d2_like}).get_notes()
        stored = row.get("_notes")
        if stored == note:
            continue
        if stored and normalise_vault_description(stored) not in legacy_notes(spec.vault_address):
            preserved.append(spec.as_string_id())
            continue
        updates[spec] = note
    for spec, note in updates.items():
        database.rows[spec]["_notes"] = note
    context.report(inspected_rows=inspected, changed_rows=len(updates), updated_fields=len(updates), preserved_note_specs=preserved)
    context.commit_metadata()

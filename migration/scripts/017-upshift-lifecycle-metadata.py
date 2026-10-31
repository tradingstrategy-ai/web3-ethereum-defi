"""Refresh Upshift lifecycle metadata and remove historical caps labelled as liquidity.

Purpose: Publish repaired admission, accepted assets and queued-withdrawal support; clear unsupported historical liquidity observations without changing prices.
Scope: The 15 public USDC-input EVM deployments frozen in repairs/upshift.py on 2026-10-30. Existing rows only.
Branch: check-upshift-deposit-adapter
PR title: fix: support Upshift through guarded vault execution
PR: Not opened.
Preserves: Every price, timestamp, row identity, discovery boundary, reader state and cache. Other vaults and fields remain unchanged.
Expected: Reviewed metadata rows refreshed and their historical available_liquidity values replaced with null. Preview reports exact counts.

No early-price backfill is needed: this repair creates no vaults and preserves all
existing price history. No schema/type changes or historical state reads occur.
LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific production input. Normal chain RPC
configuration is needed for current metadata reads; scanners must be stopped.
"""

import tempfile
from pathlib import Path

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import _publish_staged
from eth_defi.vault_protocols.repairs.upshift import refresh_upshift_metadata, stage_upshift_liquidity_correction


def migrate(context: MigrationContext) -> None:
    """Stage metadata and a verified column correction through the existing runner.

    The runner owns preview, locks and journalling. The existing price publisher
    preserves a hard-link backup before replacing a verified staged file.

    :param context: Guarded migration attempt and persistent data directory.
    :return: ``None`` after preview or the reviewed corrections.
    """
    context.report(**refresh_upshift_metadata(context.metadata()))
    source = context.data_dir / "vault-prices-1h.parquet"
    with tempfile.TemporaryDirectory(prefix="upshift-liquidity-", dir=context.data_dir) as temporary:
        destination = Path(temporary) / source.name
        changed = stage_upshift_liquidity_correction(source, destination)
        context.report(cleared_liquidity_observations=changed)
        if not context.dry_run and changed:
            _publish_staged(context, source, destination)
    context.commit_metadata()

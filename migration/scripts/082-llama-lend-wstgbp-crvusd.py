"""Onboard the Llama Lend wstGBP / crvUSD market before lead discovery reaches it.

Purpose: Lead discovery caches its result for seven days. Ethereum discovery
last completed on 2026-11-14 at block 26,416,318, before this market's first
deposit, so the scanner has no lead, row or prices for it, and the market is
missing from the export until the cache expires. Seed its lead and metadata
row, then backfill its hourly prices from the first deposit.
Scope: Ethereum Llama Lend vault 0xe279c1c1c246b6f8f32c55b7820c2164151c5570
(controller 0x195c8abf5253bf53ed4476997c7f7069566f2373, factory
0x8f6b56ec5ddf1f2691a1059f1d3cd97ac9eab0bd), deployed at block 26,384,147.
Its first ``Deposit`` event is at block 26,436,263 (2026-11-17 17:29:11 UTC),
read through Hypersync on 2026-11-19, with five deposits and one withdrawal
by then. Prices run from that block to the Ethereum price cursor plus one,
exclusive.
Branch: onboard-llamalend-wstgbp-crvusd
PR title: fix: onboard Llama Lend wstGBP / crvUSD market before lead discovery
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/NUMBER
Preserves: The lead cursor, all other leads and rows, reader state, other
vaults' prices and timestamp caches. The lead starts with zero flow counts,
because discovery adds this vault's events when it resumes from the cursor.
Expected: One inserted lead and row, then one backfilled vault of about
5,000 crvUSD TVL. A no-op for metadata if discovery has already found it.

LLM-generated operational migration; changes require human review.
DRY_RUN is the only migration-specific operator input. Requires
``JSON_RPC_ETHEREUM`` (archive), Hypersync credentials and the dense Ethereum
timestamp cache.
"""

import datetime

from eth_typing import HexAddress

from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs import vault_onboarding
from eth_defi.vault_protocols.repairs.vault_onboarding import VaultOnboardingTarget

#: Reviewed market and its first Deposit event.
TARGETS = (
    VaultOnboardingTarget(
        chain_id=1,
        vault_address=HexAddress("0xe279c1c1c246b6f8f32c55b7820c2164151c5570"),
        first_seen_at_block=26_436_263,
        first_seen_at=datetime.datetime(2026, 11, 17, 17, 29, 11),
        expected_protocol="Llama Lend",
    ),
)


def migrate(context: MigrationContext) -> None:
    """Seed the reviewed market, then backfill its prices.

    The context owns preview staging, backups and atomic persistence; the
    shared price backfill helper stages, validates and publishes the raw
    price file.

    :param context: Guarded migration attempt and shared metadata database.
    :return: ``None`` after committing the staged row and reporting the backfill.
    """
    vault_onboarding.seed_vault_rows(context, TARGETS)
    vault_onboarding.backfill_onboarded_vault_prices(context, TARGETS)

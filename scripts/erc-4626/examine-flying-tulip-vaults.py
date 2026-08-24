"""Examine the complete Flying Tulip historical-context backfill.

The script is read-only. It validates the event provenance which drives the
non-redeemable, FT-reward-reinvested ``share_price_equivalence`` series before
the ordinary vault scanner writes common Parquet observations.

Set ``CONTEXT_DATABASE`` to inspect another contextual DuckDB. The default is
the shared pipeline ``vault-historical-context.duckdb``. The reviewed dormant
BNB Chain deployment is accepted as empty; set ``REQUIRE_ALL_CHAINS=false``
only when inspecting a deliberately bounded or in-progress backfill.
"""

import math
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb
from eth_typing import HexAddress
from tabulate import tabulate

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import (
    FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP,
    FLYING_TULIP_DORMANT_CHAIN_IDS,
    FLYING_TULIP_MAX_ORACLE_AGE_SECONDS,
    FLYING_TULIP_SFTUSD_BY_CHAIN,
)
from eth_defi.erc_4626.vault_protocol.flying_tulip.historical_context import FlyingTulipHistoricalContextStore
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import get_pipeline_data_dir


@dataclass(slots=True, frozen=True)
class ChainExamination:
    """Structural and replay diagnostics for one sftUSD deployment."""

    #: Source EVM chain identifier.
    chain_id: int
    #: Official sftUSD proxy which was inspected.
    vault_address: HexAddress
    #: Count of all stored ``EpochSettled`` source events.
    source_epoch_count: int
    #: Count of tracked post-Curve ``EpochSettled`` events.
    epoch_count: int
    #: Count of missing or out-of-order epoch IDs.
    epoch_gaps: int
    #: Count of zero-address ERC-20 supply changes.
    supply_event_count: int
    #: Count of minted supply changes.
    mint_count: int
    #: Count of burned supply changes.
    burn_count: int
    #: Earliest tracked source block, if any.
    first_block: int | None
    #: Latest tracked source block, if any.
    last_block: int | None
    #: Settlements without a usable preceding Curve price.
    missing_reward_prices: int
    #: Valid contextual reader observations.
    replay_rows: int
    #: Latest reward-reinvested ftUSD share-price equivalent.
    final_share_price: Decimal | None
    #: Latest replayed principal-plus-reward ftUSD equivalent TVL.
    final_equivalent_tvl: Decimal | None
    #: Annualised gross reward-equivalent return over the replay period.
    cagr: Decimal | None
    #: Replay-invariant failure, if any.
    error: str | None


def _fetch_chain_examination(context_path: Path, chain_id: int, vault_address: HexAddress) -> ChainExamination:
    """Read source coverage and replay the price-equivalence curve once.

    The source table is checked with SQL, while the production contextual
    reader supplies the stronger economic validation: contiguous epochs,
    non-negative reconstructed supply, reward-rate identity, stake-seconds
    bounds and reward-price availability.

    :param context_path:
        Read-only shared contextual DuckDB path.
    :param chain_id:
        Source EVM chain identifier.
    :param vault_address:
        Official sftUSD proxy address.
    :return:
        Complete examination result for one deployment.
    """

    address = vault_address.lower()
    with FlyingTulipHistoricalContextStore(context_path, read_only=True) as store:
        connection = store.connection
        source_epoch_count = connection.execute(
            "SELECT COUNT(*) FROM flying_tulip_epoch_context WHERE chain_id = ? AND vault_address = ?",
            (chain_id, address),
        ).fetchone()[0]
        epoch_count, first_block, last_block, epoch_gaps = connection.execute(
            """
        WITH ordered AS (
            SELECT epoch_id, block_number, LAG(epoch_id) OVER (ORDER BY block_number, log_index) AS previous_epoch_id
            FROM flying_tulip_epoch_context
            WHERE chain_id = ? AND vault_address = ? AND block_timestamp >= ?
        )
        SELECT
            COUNT(*),
            MIN(block_number),
            MAX(block_number),
            COALESCE(SUM(CASE WHEN previous_epoch_id IS NOT NULL AND epoch_id != previous_epoch_id + 1 THEN 1 ELSE 0 END), 0)
        FROM ordered
        """,
            (chain_id, address, FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP),
        ).fetchone()
        supply_event_count, mint_count, burn_count = connection.execute(
            """
        SELECT
            COUNT(*),
            COALESCE(SUM(CASE WHEN is_mint THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN NOT is_mint THEN 1 ELSE 0 END), 0)
        FROM flying_tulip_supply_context
        WHERE chain_id = ? AND vault_address = ?
        """,
            (chain_id, address),
        ).fetchone()
        missing_reward_prices = connection.execute(
            """
        SELECT COUNT(*)
        FROM flying_tulip_epoch_context AS epoch
        ASOF LEFT JOIN flying_tulip_reward_price_context AS price
          ON epoch.block_timestamp >= price.block_timestamp
        WHERE epoch.chain_id = ?
          AND epoch.vault_address = ?
          AND epoch.block_timestamp >= ?
          AND (
              price.raw_ft_price_in_ftusd IS NULL
              OR CAST(price.raw_ft_price_in_ftusd AS HUGEINT) <= 0
              OR epoch.block_timestamp - price.oracle_updated_at > ?
          )
        """,
            (chain_id, address, FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP, FLYING_TULIP_MAX_ORACLE_AGE_SECONDS),
        ).fetchone()[0]
        replay_rows = 0
        final_share_price: Decimal | None = None
        final_equivalent_tvl: Decimal | None = None
        cagr: Decimal | None = None
        error: str | None = None
        if epoch_count:
            try:
                observations = tuple(store.iter_share_price_observations(chain_id, vault_address, 6, 18, int(first_block), int(last_block) + 1, 1))
                replay_rows = len(observations)
                if observations:
                    first_observation = observations[0]
                    final_observation = observations[-1]
                    final_share_price = final_observation.share_price
                    final_equivalent_tvl = final_observation.total_assets
                    elapsed_seconds = final_observation.block_timestamp - first_observation.block_timestamp
                    if elapsed_seconds >= 3 * 24 * 60 * 60 and first_observation.share_price > 0:
                        cagr = Decimal(str(math.pow(float(final_observation.share_price / first_observation.share_price), (365 * 24 * 60 * 60) / elapsed_seconds) - 1))
            except ValueError as exc:
                error = str(exc)
    return ChainExamination(
        chain_id=chain_id,
        vault_address=vault_address,
        source_epoch_count=int(source_epoch_count),
        epoch_count=int(epoch_count),
        epoch_gaps=int(epoch_gaps),
        supply_event_count=int(supply_event_count),
        mint_count=int(mint_count),
        burn_count=int(burn_count),
        first_block=int(first_block) if first_block is not None else None,
        last_block=int(last_block) if last_block is not None else None,
        missing_reward_prices=int(missing_reward_prices),
        replay_rows=replay_rows,
        final_share_price=final_share_price,
        final_equivalent_tvl=final_equivalent_tvl,
        cagr=cagr,
        error=error,
    )


def main() -> None:
    """Print Flying Tulip source, price and replay diagnostics.

    Missing chains, duplicate keys, incomplete price coverage, stale price
    provenance and contextual-reader invariant failures cause a non-zero exit.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    pipeline_dir = get_pipeline_data_dir()
    context_path = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    require_all_chains = os.environ.get("REQUIRE_ALL_CHAINS", "true").lower() == "true"
    if not context_path.exists():
        raise FileNotFoundError(context_path)
    connection = duckdb.connect(str(context_path), read_only=True)
    try:
        table_names = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        required_tables = {"flying_tulip_epoch_context", "flying_tulip_supply_context", "flying_tulip_reward_price_context"}
        missing_tables = required_tables - table_names
        if missing_tables:
            raise RuntimeError(f"Flying Tulip context tables are missing: {', '.join(sorted(missing_tables))}")
    finally:
        connection.close()
    examinations = [_fetch_chain_examination(context_path, chain_id, vault_address) for chain_id, vault_address in sorted(FLYING_TULIP_SFTUSD_BY_CHAIN.items())]
    connection = duckdb.connect(str(context_path), read_only=True)
    try:
        duplicate_epochs, duplicate_supply = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) - COUNT(DISTINCT (chain_id, transaction_hash, log_index)) FROM flying_tulip_epoch_context),
                (SELECT COUNT(*) - COUNT(DISTINCT (chain_id, transaction_hash, log_index)) FROM flying_tulip_supply_context)
            """
        ).fetchone()
        price_count, stale_prices, invalid_prices = connection.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(CASE WHEN block_timestamp - oracle_updated_at > ? THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN CAST(raw_oracle AS HUGEINT) <= 0 OR CAST(raw_ft_price_in_ftusd AS HUGEINT) <= 0 THEN 1 ELSE 0 END), 0)
            FROM flying_tulip_reward_price_context
            """,
            (FLYING_TULIP_MAX_ORACLE_AGE_SECONDS,),
        ).fetchone()
    finally:
        connection.close()

    summaries = [
        (
            examination.chain_id,
            examination.vault_address,
            f"{examination.source_epoch_count}/{examination.epoch_count}",
            examination.supply_event_count,
            f"{examination.mint_count}/{examination.burn_count}",
            examination.first_block or "-",
            examination.last_block or "-",
            examination.epoch_gaps,
            examination.missing_reward_prices,
            examination.replay_rows,
            f"{examination.final_share_price:.12f}" if examination.final_share_price is not None else "-",
            f"{examination.final_equivalent_tvl:,.2f}" if examination.final_equivalent_tvl is not None else "-",
            f"{examination.cagr:.2%}" if examination.cagr is not None else "N/A",
            examination.error or "OK",
        )
        for examination in examinations
    ]
    print(
        tabulate(
            summaries,
            headers=("Chain", "sftUSD", "Source/tracked epochs", "Supply", "Mint/burn", "First tracked block", "Last tracked block", "Epoch gaps", "Missing price", "Replay rows", "Final equivalent", "Equivalent TVL", "CAGR", "Reader result"),
            tablefmt="rounded_outline",
        ),
    )
    print(f"Curve price provenance: rows={price_count}; stale={stale_prices}; invalid={invalid_prices}; duplicate epoch keys={duplicate_epochs}; duplicate supply keys={duplicate_supply}")
    failures = int(duplicate_epochs) + int(duplicate_supply) + int(stale_prices) + int(invalid_prices)
    for examination in examinations:
        if require_all_chains and examination.chain_id not in FLYING_TULIP_DORMANT_CHAIN_IDS and not examination.source_epoch_count:
            failures += 1
        if examination.epoch_gaps or examination.missing_reward_prices or examination.error:
            failures += 1
        if examination.replay_rows != examination.epoch_count:
            failures += 1
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

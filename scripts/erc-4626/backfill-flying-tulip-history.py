"""Backfill Flying Tulip sftUSD source history and supported reward prices.

The script streams real ``EpochSettled`` and sftUSD mint/burn events through
Hypersync for Ethereum, BNB Chain and Sonic. It stores replayable source
evidence and historical Ethereum Curve FT/ftUSD oracle provenance in the
shared historical-context DuckDB; price-equivalent Parquet rows are written
later by the contextual vault reader. Source events begin at each proxy's
deployment so post-Curve supply is exact, while reward-price tracking and
performance begin only after the canonical Curve market was deployed.

Optional environment variables:

- ``CONTEXT_DATABASE``: Override the shared contextual DuckDB path.
- ``SOURCE_CHUNK_SIZE``: Hypersync range per committed source chunk.
- ``FLYING_TULIP_HYPERSYNC_RPM``: Conservative Hypersync request rate.
- ``FLYING_TULIP_HYPERSYNC_CONCURRENCY``: Hypersync in-flight stream count.
- ``DRY_RUN``: Write an isolated temporary context database instead.
"""

import logging
import os
import tempfile
from pathlib import Path

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_CURVE_CANONICAL_START_BLOCK, FLYING_TULIP_SFTUSD_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.flying_tulip.historical_context import FLYING_TULIP_SOURCE_CHUNK_SIZE, FlyingTulipContextPrefillResult, fetch_and_store_flying_tulip_source_history, fetch_flying_tulip_proxy_deployment_block
from eth_defi.erc_4626.vault_protocol.flying_tulip.reward_price import fetch_and_store_flying_tulip_reward_prices
from eth_defi.hypersync.utils import HypersyncBackendConfig, configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Public backwards-compatible alias for the focused script test and operators.
fetch_contract_deployment_block = fetch_flying_tulip_proxy_deployment_block


def configure_flying_tulip_backfill_hypersync(web3: Web3) -> HypersyncBackendConfig:
    """Create a quota-safe Hypersync client for the long-running backfill.

    The generic scanner defaults favour short concurrent scans. This long
    source and timestamp backfill defaults to one in-flight request and 20
    RPM. Operators with a larger documented quota may override either generic
    ``HYPERSYNC_*`` setting.

    :param web3:
        Connected source-chain provider.
    :return:
        Configured, throttle-aware Hypersync backend.
    """

    requests_per_minute = os.environ.setdefault("HYPERSYNC_RPM", os.environ.get("FLYING_TULIP_HYPERSYNC_RPM", "20"))
    concurrency = os.environ.setdefault("HYPERSYNC_CONCURRENCY", os.environ.get("FLYING_TULIP_HYPERSYNC_CONCURRENCY", "1"))
    logger.info("Flying Tulip backfill Hypersync settings: rpm=%s concurrency=%s", requests_per_minute, concurrency)
    return configure_hypersync_from_env(web3)


def fetch_flying_tulip_full_backfill_range(web3: Web3, vault_address: HexAddress) -> tuple[int, int]:
    """Return the proxy-genesis-to-safe-head range used for one source replay.

    :param web3:
        Configured Web3 provider for a supported chain.
    :return:
        Inclusive sftUSD proxy deployment block and exclusive safe head.
    """

    end_block = get_almost_latest_block_number(web3)
    return fetch_contract_deployment_block(web3, vault_address, end_block), end_block


def _run_chain(chain_id: int, context_path: Path, source_chunk_size: int) -> tuple[FlyingTulipContextPrefillResult, Web3, HypersyncBackendConfig]:
    """Backfill one official sftUSD deployment into the supplied cache.

    :param chain_id:
        EVM chain identifier for a reviewed deployment.
    :param context_path:
        Target shared or temporary contextual database.
    :param source_chunk_size:
        Observable Hypersync block range size.
    :return:
        Source result, Web3 provider and configured Hypersync backend.
    """

    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    start_block, end_block = fetch_flying_tulip_full_backfill_range(web3, FLYING_TULIP_SFTUSD_BY_CHAIN[chain_id])
    configured_hypersync = configure_flying_tulip_backfill_hypersync(web3)
    result = fetch_and_store_flying_tulip_source_history(
        web3=web3,
        hypersync_client=configured_hypersync.hypersync_client,
        start_block=start_block,
        end_block=end_block,
        context_path=context_path,
        source_chunk_size=source_chunk_size,
    )
    print(f"Flying Tulip chain={result.chain_id} blocks=[{result.start_block}, {result.end_block}) epochs={result.epochs_fetched} supply_events={result.supply_events_fetched} inserted={result.rows_inserted}")
    return result, web3, configured_hypersync


def _run_all_chains(context_path: Path, source_chunk_size: int) -> None:
    """Collect raw sources, then price every chain from the snapped Ethereum head.

    Ethereum source collection happens first so the price mapper has a
    conservative timestamp-cache lower boundary. The same Ethereum safe head
    and shared rate-limited client are then used for all cross-chain joins.

    :param context_path:
        Shared or isolated contextual-cache database.
    :param source_chunk_size:
        Maximum source blocks per Hypersync request.
    :return:
        None.
    """

    ethereum_result, ethereum_web3, ethereum_hypersync = _run_chain(1, context_path, source_chunk_size)
    for chain_id in FLYING_TULIP_SFTUSD_BY_CHAIN:
        if chain_id != 1:
            _run_chain(chain_id, context_path, source_chunk_size)
    for chain_id in FLYING_TULIP_SFTUSD_BY_CHAIN:
        price_result = fetch_and_store_flying_tulip_reward_prices(
            ethereum_web3=ethereum_web3,
            ethereum_hypersync_client=ethereum_hypersync.hypersync_client,
            chain_id=chain_id,
            ethereum_start_block=FLYING_TULIP_CURVE_CANONICAL_START_BLOCK,
            ethereum_end_block=ethereum_result.end_block,
            context_path=context_path,
        )
        print(f"Flying Tulip reward prices chain={chain_id} epochs={price_result.epochs_considered} resolved={price_result.price_blocks_resolved} inserted={price_result.prices_inserted} stale={price_result.prices_stale}")


def main() -> None:
    """Run a complete source-history backfill for every reviewed deployment.

    The safe head is resolved once per chain. The process has no reader-state
    dependency and serialises with the scanner pipeline writer lock.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    source_chunk_size = int(os.environ.get("SOURCE_CHUNK_SIZE", str(FLYING_TULIP_SOURCE_CHUNK_SIZE)))
    pipeline_dir = get_pipeline_data_dir()
    context_path = Path(os.environ.get("CONTEXT_DATABASE", pipeline_dir / "vault-historical-context.duckdb")).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    if dry_run:
        with tempfile.TemporaryDirectory(prefix="flying-tulip-backfill-") as directory:
            _run_all_chains(Path(directory) / context_path.name, source_chunk_size)
        print("Dry run complete; production context database was not changed")
        return
    with wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60):
        _run_all_chains(context_path, source_chunk_size)


if __name__ == "__main__":
    main()

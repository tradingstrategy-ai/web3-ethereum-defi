"""Real-provider Rysk Premium epoch-finalisation coverage."""

import os
from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.rysk.historical_context import RyskHistoricalContextStore, fetch_and_store_rysk_premium_history, fetch_rysk_finalised_epoch_prices
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

RYSK_HYPEREVM_POOL = HexAddress("0x0fe45639d2d4f8c3c999946a44c287fcff5fa541")
RYSK_FINAL_EPOCH = 7
RYSK_FINAL_RAW_PPS = 1_031_278
RYSK_EPOCH_7_PRICE_SET_BLOCK = 36_387_728
RYSK_EPOCH_7_EXECUTION_BLOCK = 36_388_069

pytestmark = pytest.mark.skipif(
    JSON_RPC_HYPERLIQUID is None or HYPERSYNC_API_KEY is None,
    reason="JSON_RPC_HYPERLIQUID and HYPERSYNC_API_KEY are required for the real Rysk Hypersync integration test",
)


def test_rysk_price_becomes_observable_at_epoch_execution(tmp_path: Path) -> None:
    """Use the final execution block rather than the earlier proposal block.

    This range contains a real ``EpochPriceSet`` for epoch 7 followed by
    ``epochExecuted(8)``. The assertion protects the equity curve against the
    former application snapshot behaviour, which timestamped the proposal as
    if it were already final. Replaying the stored execution boundary also
    verifies that an incremental scheduled refresh does not require the older
    proposal to be present in its narrower source window.

    :param tmp_path:
        Isolated persistent timestamp-cache directory.
    :return:
        None.
    """

    assert JSON_RPC_HYPERLIQUID is not None
    web3 = create_multi_provider_web3(JSON_RPC_HYPERLIQUID)
    client = configure_hypersync_from_env(web3).hypersync_client
    assert client is not None
    observations = fetch_rysk_finalised_epoch_prices(
        hypersync_client=client,
        chain_id=999,
        pool_address=RYSK_HYPEREVM_POOL,
        start_block=RYSK_EPOCH_7_PRICE_SET_BLOCK,
        end_block=RYSK_EPOCH_7_EXECUTION_BLOCK + 1,
        timestamp_cache_path=tmp_path / "block-timestamp",
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.epoch == RYSK_FINAL_EPOCH
    assert observation.block_number == RYSK_EPOCH_7_EXECUTION_BLOCK
    assert observation.block_number != RYSK_EPOCH_7_PRICE_SET_BLOCK
    assert observation.raw_deposit_pps == RYSK_FINAL_RAW_PPS
    assert observation.raw_withdrawal_pps == RYSK_FINAL_RAW_PPS

    context_path = tmp_path / "context.duckdb"
    with RyskHistoricalContextStore(context_path) as store:
        assert store.insert_observations(observations) == (1, 1)

    replay = fetch_and_store_rysk_premium_history(
        web3=web3,
        hypersync_client=client,
        pool_start_blocks={RYSK_HYPEREVM_POOL: RYSK_EPOCH_7_PRICE_SET_BLOCK},
        end_block=RYSK_EPOCH_7_EXECUTION_BLOCK + 1,
        context_path=context_path,
        timestamp_cache_path=tmp_path / "block-timestamp",
    )
    assert replay.observations_fetched == 0
    assert replay.observations_inserted == 0

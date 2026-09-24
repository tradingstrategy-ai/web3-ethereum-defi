"""Real-provider integration coverage for Flying Tulip source collection."""

import asyncio
import os
from pathlib import Path

import pytest

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_SFTUSD_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.flying_tulip.historical_context import FlyingTulipHistoricalContextStore, fetch_source_chunk
from eth_defi.erc_4626.vault_protocol.flying_tulip.reward_price import fetch_and_store_flying_tulip_reward_prices
from eth_defi.erc_4626.vault_protocol.flying_tulip.vault import FlyingTulipVault
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM is required for the real Flying Tulip Hypersync integration test")

#: Ethereum block containing Flying Tulip sftUSD epoch 264 settlement.
FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK = 25_822_053

#: Epoch settled at :data:`FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK`.
FLYING_TULIP_ETHEREUM_EPOCH_ID = 264


def test_fetch_real_epoch_settlement_through_hypersync() -> None:
    """Stream a real sftUSD epoch event without mocks.

    The test exercises the configured Hypersync endpoint, real Ethereum source
    block and the production event decoder.

    :return:
        None.
    """

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    configured_hypersync = configure_hypersync_from_env(web3)
    with asyncio.Runner() as runner:
        epochs, supply_changes = runner.run(
            fetch_source_chunk(
                configured_hypersync.hypersync_client,
                1,
                FLYING_TULIP_SFTUSD_BY_CHAIN[1],
                FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK,
                FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK + 1,
            )
        )

    assert len(epochs) == 1
    event = epochs[0]
    assert event.epoch_id == FLYING_TULIP_ETHEREUM_EPOCH_ID
    assert event.block_number == FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK
    assert event.raw_reward_amount > 0
    assert event.raw_stake_time > 0
    assert event.raw_rate_ray > 0
    assert all(change.block_number == FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK for change in supply_changes)


def test_real_flying_tulip_adapter_keeps_principal_and_reward_semantics() -> None:
    """Exercise the reviewed live vault adapter against real Ethereum state.

    This is deliberately a real provider test, not a mocked ABI fixture. It
    verifies the exact fixed-price principal surface, reward-token source and
    contextual-reader configuration used by the common historical scanner.

    :return:
        None.
    """

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    vault = create_vault_instance(
        web3,
        FLYING_TULIP_SFTUSD_BY_CHAIN[1],
        {ERC4626Feature.flying_tulip_like, ERC4626Feature.share_price_equivalence},
    )

    assert isinstance(vault, FlyingTulipVault)
    assert vault.fetch_share_price("latest") == 1
    assert vault.fetch_total_assets("latest") == vault.fetch_total_supply("latest")
    assert vault.reward_token.symbol == "FT"
    assert vault.get_historical_reader(stateful=True).uses_contextual_history
    assert vault.get_historical_reader(stateful=True).uses_share_price_equivalence
    assert vault.get_deposit_manager_capability() is None


def test_real_curve_price_prefill_uses_hypersync_timestamp_and_archive_state(tmp_path: Path) -> None:
    """Store real source provenance and its actual historical Curve price.

    The test deliberately uses the settlement's real block identity rather
    than a fabricated timestamp or RPC/Hypersync mock. It verifies the
    source-table → dense timestamp cache → archive ``price_oracle()`` path.

    :param tmp_path:
        Isolated context and timestamp-cache directory.
    :return:
        None.
    """

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    configured_hypersync = configure_hypersync_from_env(web3)
    with asyncio.Runner() as runner:
        epochs, _supply_changes = runner.run(
            fetch_source_chunk(
                configured_hypersync.hypersync_client,
                1,
                FLYING_TULIP_SFTUSD_BY_CHAIN[1],
                FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK,
                FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK + 1,
            )
        )
    context_path = tmp_path / "vault-historical-context.duckdb"
    with FlyingTulipHistoricalContextStore(context_path) as store:
        assert store.insert_epoch(epochs[0])
    result = fetch_and_store_flying_tulip_reward_prices(
        ethereum_web3=web3,
        ethereum_hypersync_client=configured_hypersync.hypersync_client,
        chain_id=1,
        ethereum_start_block=FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK - 1,
        ethereum_end_block=FLYING_TULIP_ETHEREUM_EPOCH_264_BLOCK + 1,
        context_path=context_path,
        timestamp_cache_path=tmp_path / "block-timestamp",
    )
    with FlyingTulipHistoricalContextStore(context_path) as store:
        row = store.connection.execute("SELECT raw_oracle, raw_ft_price_in_ftusd FROM flying_tulip_reward_price_context").fetchone()

    assert result.epochs_considered == 1
    assert result.price_blocks_resolved == 1
    assert result.prices_inserted == 1
    assert result.prices_stale == 0
    assert int(row[0]) > 0
    assert int(row[1]) > 0

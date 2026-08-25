"""Tests for the Rysk Premium DeFi option-pool adapter."""

import asyncio
import logging
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.classification import create_probe_calls, identify_vault_features
from eth_defi.erc_4626.core import ERC4626Feature, passes_price_scan_activity_filter
from eth_defi.erc_4626.discovery_base import VaultEventKind, get_rysk_premium_discovery_events, get_vault_event_topic_map
from eth_defi.erc_4626.vault_protocol.rysk import historical_context
from eth_defi.erc_4626.vault_protocol.rysk.api import RYSK_PREMIUM_API_TIMEOUT, RYSK_PREMIUM_POOLS_URL, RyskPremiumAPIError, RyskPremiumPool, fetch_rysk_premium_pools, is_rysk_premium_test_pool
from eth_defi.erc_4626.vault_protocol.rysk.historical_context import RYSK_EPOCH_PRICE_SET_TOPIC, RyskHistoricalContextStore, RyskHistoricalSharePriceObservation, fetch_and_store_rysk_premium_history, fetch_rysk_finalised_epoch_prices
from eth_defi.erc_4626.vault_protocol.rysk.vault import RyskVault
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.price_source import PriceSource

RYSK_KPK_WETH_PUT = HexAddress("0x1195826418541cb3e80a22ef5736a6794393c91a")
USDC_ADDRESS_WORD = bytes.fromhex("00" * 12 + "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
RYSK_FINAL_EPOCH = 7
RYSK_FINAL_EXECUTION_BLOCK = 36_388_069


def test_rysk_uses_onchain_lead_and_feature_probes() -> None:
    """Require both the custom deposit lead and Rysk accounting surface.

    :return:
        None.
    """

    web3 = Web3()
    events = get_rysk_premium_discovery_events(web3)
    topic_map = get_vault_event_topic_map(web3)
    assert events[0].event_name == "EpochPriceSet"
    event_topic = get_topic_signature_from_event(events[0])
    assert event_topic == RYSK_EPOCH_PRICE_SET_TOPIC
    assert event_topic != f"0x{Web3.keccak(text='Deposit(address,uint256,uint256)').hex()}"
    assert topic_map[event_topic] == VaultEventKind.configuration

    for chain_id in (1, 999):
        supported_web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id, contract=web3.eth.contract))
        supported_events = get_rysk_premium_discovery_events(supported_web3)
        assert supported_events[0].event_name == "EpochPriceSet"

    unsupported_web3 = Mock()
    unsupported_web3.eth.chain_id = 42161
    assert get_rysk_premium_discovery_events(unsupported_web3) == []

    for chain_id in (1, 999):
        probe_names = {call.func_name for call in create_probe_calls([RYSK_KPK_WETH_PUT], chain_id=chain_id)}
        assert {"collateralAllocated", "collateralAsset"} <= probe_names
    unsupported_probe_names = {call.func_name for call in create_probe_calls([RYSK_KPK_WETH_PUT], chain_id=42161)}
    assert "collateralAllocated" not in unsupported_probe_names
    assert "collateralAsset" not in unsupported_probe_names

    low_activity_detection = SimpleNamespace(features={ERC4626Feature.rysk_premium_like}, deposit_count=0)
    assert passes_price_scan_activity_filter(low_activity_detection, min_deposit_threshold=5)


def test_rysk_feature_classification_excludes_internal_pools() -> None:
    """Classify the verified non-ERC-4626 surface and reject internal names.

    :return:
        None.
    """

    failed = SimpleNamespace(success=False, result=b"")
    successful_word = SimpleNamespace(success=True, result=b"\x00" * 32)
    successful_address = SimpleNamespace(success=True, result=USDC_ADDRESS_WORD)

    def create_calls(name: bytes) -> dict[str, SimpleNamespace]:
        """Create the result prefix consumed before non-ERC-4626 return.

        :param name:
            Raw ABI name bytes used by the internal-product guard.
        :return:
            Minimal classification result mapping.
        """

        return {
            "EVM IS BROKEN SHIT": failed,
            "shareManager": failed,
            "getAssetCount": failed,
            "collateralAllocated": successful_word,
            "collateralAsset": successful_address,
            "name": SimpleNamespace(success=True, result=name),
            "assetsWhitelistAddress": failed,
            "convertToShares": failed,
        }

    assert identify_vault_features(RYSK_KPK_WETH_PUT, create_calls(b"USDC-WETH-KPK-Put-Ethereum"), None, chain_id=1) == {
        ERC4626Feature.rysk_premium_like,
        ERC4626Feature.share_price_equivalence,
    }
    assert identify_vault_features(RYSK_KPK_WETH_PUT, create_calls(b"Rysk Internal Test"), None, chain_id=1) == {ERC4626Feature.broken}


def test_rysk_feature_classification_does_not_override_erc_4626() -> None:
    """Do not classify an ERC-4626 contract from collateral accessors alone.

    :return:
        None.
    """

    failed = SimpleNamespace(success=False, result=b"")
    successful_word = SimpleNamespace(success=True, result=b"\x00" * 32)
    successful_address = SimpleNamespace(success=True, result=USDC_ADDRESS_WORD)
    calls = defaultdict(lambda: failed, {call.func_name: failed for call in create_probe_calls([RYSK_KPK_WETH_PUT], chain_id=1)})
    calls.update(
        {
            "collateralAllocated": successful_word,
            "collateralAsset": successful_address,
            "convertToShares": successful_word,
            "name": SimpleNamespace(success=True, result=b"Unrelated ERC-4626"),
        }
    )

    features = identify_vault_features(RYSK_KPK_WETH_PUT, calls, None, chain_id=1)

    assert ERC4626Feature.rysk_premium_like not in features
    assert ERC4626Feature.share_price_equivalence not in features


def test_rysk_adapter_is_not_a_tokenised_fund() -> None:
    """Keep Rysk off the legal-fund adapter and flag surfaces.

    :return:
        None.
    """

    vault = RyskVault(Web3(), VaultSpec(chain_id=1, vault_address=RYSK_KPK_WETH_PUT))

    assert isinstance(vault, VaultBase)
    assert not isinstance(vault, TokenisedFundVault)
    assert VaultFlag.tokenised_fund not in vault.get_flags()
    assert vault.get_share_price_source() is PriceSource.smart_contract_event


def test_rysk_history_persists_only_execution_provenance(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Scale and deduplicate a price at its final execution block.

    :param tmp_path:
        Temporary directory for the isolated DuckDB context database.
    :param caplog:
        Captured warning for a non-positive bootstrap price.
    :return:
        None.
    """

    observation = RyskHistoricalSharePriceObservation(
        chain_id=999,
        pool_address=RYSK_KPK_WETH_PUT,
        epoch=RYSK_FINAL_EPOCH,
        block_number=RYSK_FINAL_EXECUTION_BLOCK,
        block_timestamp=1_700_000_000,
        transaction_hash="0x" + "1" * 64,
        log_index=8,
        raw_deposit_pps=1_031_278,
        raw_withdrawal_pps=1_031_278,
    )
    zero_observation = RyskHistoricalSharePriceObservation(
        chain_id=999,
        pool_address=RYSK_KPK_WETH_PUT,
        epoch=RYSK_FINAL_EPOCH + 1,
        block_number=RYSK_FINAL_EXECUTION_BLOCK + 1,
        block_timestamp=1_700_000_001,
        transaction_hash="0x" + "4" * 64,
        log_index=9,
        raw_deposit_pps=0,
        raw_withdrawal_pps=0,
    )

    with RyskHistoricalContextStore(tmp_path / "history.duckdb") as store:
        assert store.insert_observations((observation,)) == (1, 1)
        assert store.insert_observations((observation,)) == (1, 0)
        assert store.insert_observations((zero_observation,)) == (1, 1)
        assert store.fetch_next_source_block(999, RYSK_KPK_WETH_PUT, 1) == zero_observation.block_number
        assert store.fetch_source_ids_at_block(999, RYSK_KPK_WETH_PUT, RYSK_FINAL_EXECUTION_BLOCK) == frozenset({f"999:{observation.transaction_hash}:8"})
        with caplog.at_level(logging.WARNING):
            observations = tuple(
                store.iter_finalised_share_prices(
                    chain_id=999,
                    pool_address=RYSK_KPK_WETH_PUT,
                    start_block=36_388_000,
                    end_block=36_389_000,
                    collateral_decimals=6,
                )
            )

    assert len(observations) == 1
    assert observations[0].epoch == RYSK_FINAL_EPOCH
    assert observations[0].block_number == RYSK_FINAL_EXECUTION_BLOCK
    assert observations[0].withdrawal_share_price == Decimal("1.031278")
    assert "Skipping non-positive Rysk withdrawal price" in caplog.text


def test_rysk_incremental_replay_handles_execution_without_price_update(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Contain known replays and discovery ranges that start mid-epoch.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    :param tmp_path:
        Isolated timestamp-cache directory.
    :param caplog:
        Captured warning for an execution whose proposal predates discovery.
    :return:
        None.
    """

    transaction_hash = "0x" + "2" * 64
    execution = RyskHistoricalSharePriceObservation(
        chain_id=999,
        pool_address=RYSK_KPK_WETH_PUT,
        epoch=RYSK_FINAL_EPOCH,
        block_number=RYSK_FINAL_EXECUTION_BLOCK,
        block_timestamp=0,
        transaction_hash=transaction_hash,
        log_index=9,
        raw_deposit_pps=0,
        raw_withdrawal_pps=0,
    )

    async def fetch_chunk(
        _hypersync_client: object,
        _chain_id: int,
        _pool_address: HexAddress,
        _start_block: int,
        _end_block: int,
    ) -> tuple[list[object], list[RyskHistoricalSharePriceObservation]]:
        """Return only the replayed execution event."""

        await asyncio.sleep(0)
        return [], [execution]

    fetch_timestamps = Mock()
    monkeypatch.setattr(historical_context, "_fetch_rysk_epoch_source_chunk", fetch_chunk)
    monkeypatch.setattr(historical_context, "fetch_exact_block_timestamps_using_hypersync_cached", fetch_timestamps)

    observations = fetch_rysk_finalised_epoch_prices(
        hypersync_client=Mock(),
        chain_id=999,
        pool_address=RYSK_KPK_WETH_PUT,
        start_block=RYSK_FINAL_EXECUTION_BLOCK,
        end_block=RYSK_FINAL_EXECUTION_BLOCK + 1,
        source_chunk_size=1,
        timestamp_cache_path=tmp_path,
        known_execution_source_ids={f"999:{transaction_hash}:9"},
    )

    assert observations == ()
    fetch_timestamps.assert_not_called()

    with caplog.at_level(logging.WARNING):
        observations = fetch_rysk_finalised_epoch_prices(
            hypersync_client=Mock(),
            chain_id=999,
            pool_address=RYSK_KPK_WETH_PUT,
            start_block=RYSK_FINAL_EXECUTION_BLOCK,
            end_block=RYSK_FINAL_EXECUTION_BLOCK + 1,
            source_chunk_size=1,
            timestamp_cache_path=tmp_path,
        )

    assert observations == ()
    assert "price update predates the source window" in caplog.text
    fetch_timestamps.assert_not_called()


def test_rysk_incremental_store_supplies_known_boundary_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pass stored boundary executions into the incremental event join.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    :param tmp_path:
        Isolated context database directory.
    :return:
        None.
    """

    transaction_hash = "0x" + "3" * 64
    observation = RyskHistoricalSharePriceObservation(999, RYSK_KPK_WETH_PUT, 7, RYSK_FINAL_EXECUTION_BLOCK, 1_700_000_000, transaction_hash, 4, 1_000_000, 1_000_000)
    context_path = tmp_path / "history.duckdb"
    with RyskHistoricalContextStore(context_path) as store:
        assert store.insert_observations((observation,)) == (1, 1)

    def fetch_prices(**kwargs: object) -> tuple[RyskHistoricalSharePriceObservation, ...]:
        """Assert that the stored cursor identity reaches the event join."""

        assert kwargs["start_block"] == RYSK_FINAL_EXECUTION_BLOCK
        assert kwargs["known_execution_source_ids"] == frozenset({f"999:{transaction_hash}:4"})
        return ()

    monkeypatch.setattr(historical_context, "fetch_rysk_finalised_epoch_prices", fetch_prices)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=999))

    result = fetch_and_store_rysk_premium_history(
        web3=web3,
        hypersync_client=Mock(),
        pool_start_blocks={RYSK_KPK_WETH_PUT: 1},
        end_block=RYSK_FINAL_EXECUTION_BLOCK + 100,
        context_path=context_path,
    )

    assert result.observations_fetched == 0
    assert result.observations_inserted == 0


def test_rysk_catalogue_test_pool_labels() -> None:
    """Keep application-labelled operational products out of reports.

    :return:
        None.
    """

    public = RyskPremiumPool(999, RYSK_KPK_WETH_PUT, "Public put", None, "put")
    internal = RyskPremiumPool(999, RYSK_KPK_WETH_PUT, "Rysk Internal 1", None, "put")
    test_only = RyskPremiumPool(999, RYSK_KPK_WETH_PUT, "Test", "For test purposes only", "call")

    assert not is_rysk_premium_test_pool(public)
    assert is_rysk_premium_test_pool(internal)
    assert is_rysk_premium_test_pool(test_only)


def test_fetch_rysk_premium_pools_validates_catalogue() -> None:
    """Parse the operator catalogue and reject malformed entries.

    :return:
        None.
    """

    response = Mock()
    response.json.return_value = [
        {
            "chainId": "999",
            "address": str(RYSK_KPK_WETH_PUT).upper().replace("0X", "0x"),
            "name": "Public put",
            "description": "Cash-secured put",
            "type": "Put",
        }
    ]
    session = Mock()
    session.get.return_value = response

    pools = fetch_rysk_premium_pools(session=session)

    assert pools == (RyskPremiumPool(999, HexAddress(str(RYSK_KPK_WETH_PUT).lower()), "Public put", "Cash-secured put", "put"),)
    session.get.assert_called_once_with(RYSK_PREMIUM_POOLS_URL, timeout=RYSK_PREMIUM_API_TIMEOUT)
    response.raise_for_status.assert_called_once_with()

    response.json.return_value = [{"chainId": "not-a-chain", "address": RYSK_KPK_WETH_PUT, "type": "put"}]
    with pytest.raises(RyskPremiumAPIError, match="chainId is not an integer"):
        fetch_rysk_premium_pools(session=session)

    response.json.return_value = [{"chainId": 999, "address": "not-an-address", "type": "put"}]
    with pytest.raises(RyskPremiumAPIError, match="address is not an EVM address"):
        fetch_rysk_premium_pools(session=session)

    response.json.return_value = [{"chainId": 999, "address": RYSK_KPK_WETH_PUT, "type": "straddle"}]
    with pytest.raises(RyskPremiumAPIError, match="unsupported option type"):
        fetch_rysk_premium_pools(session=session)

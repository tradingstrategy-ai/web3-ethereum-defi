# Exact fixed-block values and naive UTC dates are intentional.
# ruff: noqa: PLR2004, DTZ001
"""Infinit's reviewed contract identities on the shared Arbitrum fork."""

import datetime
import os
import pickle  # noqa: S403 - local test fixtures only
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from web3 import Web3
from web3.exceptions import Web3RPCError

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.infinit.api import InfinitHistory, InfinitPricePoint
from eth_defi.erc_4626.vault_protocol.infinit.constants import APEX_PRODUCT, INFINIT_USDC
from eth_defi.erc_4626.vault_protocol.infinit.events import InfinitEvent
from eth_defi.erc_4626.vault_protocol.infinit.historical_context import InfinitHistoricalContextStore
from eth_defi.erc_4626.vault_protocol.infinit.vault import InfinitVault
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.token import TokenDiskCache
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable
from eth_defi.vault.price_source import PriceSource
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.vaultdb import VaultDatabase

pytestmark = [pytest.mark.skipif(not os.environ.get("JSON_RPC_ARBITRUM"), reason="JSON_RPC_ARBITRUM required"), pytest.mark.xdist_group("fork:arbitrum:midnight")]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Use the shared read-only fixed-block fork and warm RPC cache."""
    return anvil_fork_pool.get_web3(os.environ["JSON_RPC_ARBITRUM"], ARBITRUM_MIDNIGHT_BLOCK)


def test_infinit_contract_metadata(web3: Web3) -> None:
    """Receipt supply, lifecycle, risk and transaction support are independent."""
    vault = create_vault_instance(web3, APEX_PRODUCT.address, {ERC4626Feature.infinit_like}, token_cache={})
    assert isinstance(vault, InfinitVault)
    assert vault.get_protocol_name() == "Infinit"
    assert vault.name == "APEX MultiStrategy"
    assert vault.address.lower() == APEX_PRODUCT.address
    assert vault.fetch_info()["custody_safe"] == APEX_PRODUCT.custody_safe
    assert vault.fetch_scan_record_extra_data()["_infinit_matures_at"] == datetime.datetime(2026, 10, 28, 10)
    assert vault.share_token.address.lower() == APEX_PRODUCT.share_token
    assert vault.denomination_token.address.lower() == INFINIT_USDC
    assert vault.share_token.decimals == 6
    assert vault.fetch_total_supply() == Decimal("506013.231165")
    assert vault.get_risk() == VaultTechnicalRisk.dangerous
    assert vault.get_management_fee("latest") == 0.02
    assert vault.get_performance_fee("latest") == 0.20
    assert vault.get_fee_data().fee_mode is None
    assert vault.get_share_price_source() == PriceSource.api
    assert vault.get_estimated_lock_up() == datetime.timedelta(days=90)
    assert vault.fetch_deposit_open() is None
    assert vault.fetch_redemption_closed_reason() == "Vault has not matured"
    assert vault.fetch_redemption_next_open() == datetime.datetime(2026, 10, 28, 10)
    assert vault.get_deposit_manager_capability() is None
    with pytest.raises(VaultFlowUnavailable):
        vault.get_deposit_manager()
    with pytest.raises(VaultFlowUnavailable):
        vault.get_flow_manager()


@pytest.mark.parametrize("source_error", [ValueError, TimeoutError, Web3RPCError])
def test_infinit_recurring_scan_late_prices(web3: Web3, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_error: type[Exception]) -> None:  # noqa: PLR0914
    """Late source prices survive a warm chain cursor and repeated address-scoped scans.

    Contract metadata uses the shared fixed fork. Source transport is replaced
    with deterministic observations, while the real coordinator, context and
    Parquet writer preserve unrelated rows and reader-state bytes.
    """
    end = ARBITRUM_MIDNIGHT_BLOCK
    stamp = datetime.datetime.fromtimestamp(web3.eth.get_block(end)["timestamp"], datetime.UTC).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    spec = VaultSpec(42161, APEX_PRODUCT.address)
    sentinel_spec = VaultSpec(42161, "0x" + "77" * 20)
    db = VaultDatabase()
    db.rows[spec] = {"_detection_data": ERC4262VaultDetection(42161, APEX_PRODUCT.address, APEX_PRODUCT.deployment_block, APEX_PRODUCT.deployed_at, {ERC4626Feature.infinit_like}, stamp, 28, 0)}
    metadata = tmp_path / "metadata.pickle"
    db.write(metadata)
    state = tmp_path / "reader.pickle"
    state.write_bytes(pickle.dumps({sentinel_spec: {"last_block": end, "sentinel": "preserve"}}))
    state_bytes = state.read_bytes()
    output = tmp_path / "prices.parquet"
    context = tmp_path / "history.duckdb"
    points = [InfinitPricePoint(stamp - datetime.timedelta(hours=3), Decimal("1.01")), InfinitPricePoint(stamp - datetime.timedelta(hours=1), Decimal("1.03"))]
    block_map = {stamp - datetime.timedelta(hours=i): end - i * 10000 for i in (1, 2, 3)}
    events = [InfinitEvent(APEX_PRODUCT.deployment_block, 0, 0, "created", 0, "0x1"), InfinitEvent(APEX_PRODUCT.deployment_block, 1, 0, "mint", 506013231165, "0x2")]
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")

    def prefill(**kwargs: object) -> int:
        """Persist a complete deterministic source snapshot before publication."""
        history = InfinitHistory(APEX_PRODUCT.application_id, tuple(points), "fixture", str(points))
        with InfinitHistoricalContextStore(kwargs["context_path"]) as store:
            return store.store(APEX_PRODUCT, history, points, block_map, events, APEX_PRODUCT.deployment_block, end, expected_supply=506013231165)

    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *_args, **_kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "MultiProviderWeb3Factory", lambda *_args, **_kwargs: lambda: web3)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", lambda: cache)
    monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *_args, **_kwargs: SimpleNamespace(hypersync_client=object()))
    monkeypatch.setattr(scan_all_chains, "get_almost_latest_block_number", lambda _connection: end)
    monkeypatch.setattr(scan_all_chains, "fetch_infinit_timestamp_tail", lambda *_args: None)
    monkeypatch.setattr(scan_all_chains, "fetch_and_store_infinit_history", prefill)
    monkeypatch.setattr(InfinitVault, "fetch_valuation_validation", lambda _self: {})
    options = dict(rpc_url="https://recorded.invalid", max_workers=1, frequency="1h", vault_db_path=metadata, uncleaned_price_path=output, reader_state_path=state, historical_context_path=context, end_block=end)  # noqa: C408
    try:
        success, metrics = scan_all_chains.scan_prices_for_chain(**options)
        assert success, metrics
        first = pd.read_parquet(output)
        assert len(first) == 2
        sentinel = first.iloc[[0]].copy()
        sentinel["address"] = sentinel_spec.vault_address
        pd.concat([first, sentinel]).to_parquet(output, index=False)
        # This newly published source observation predates the unrelated EVM cursor.
        points.insert(1, InfinitPricePoint(stamp - datetime.timedelta(hours=2), Decimal("1.02")))
        for _ in range(2):
            success, metrics = scan_all_chains.scan_prices_for_chain(**options)
            assert success, metrics
            prices = pd.read_parquet(output)
            own = prices[prices.address == APEX_PRODUCT.address].sort_values("timestamp")
            assert own.share_price.tolist() == pytest.approx([1.01, 1.02, 1.03])
            pd.testing.assert_frame_equal(prices[prices.address == sentinel_spec.vault_address].reset_index(drop=True), sentinel.reset_index(drop=True))
            assert state.read_bytes() == state_bytes
        before = output.read_bytes()

        def unavailable(**_kwargs: object) -> None:
            """Simulate a failed application read before the price writer runs."""
            message = "source unavailable"
            raise source_error(message)

        monkeypatch.setattr(scan_all_chains, "fetch_and_store_infinit_history", unavailable)
        success, _ = scan_all_chains.scan_prices_for_chain(**options)
        assert not success
        assert output.read_bytes() == before
        assert state.read_bytes() == state_bytes
        # A lost context plus a truncated API must never erase retained Parquet.
        context.unlink()
        del points[0]
        monkeypatch.setattr(scan_all_chains, "fetch_and_store_infinit_history", prefill)
        success, metrics = scan_all_chains.scan_prices_for_chain(**options)
        assert not success
        assert metrics["infinit_deferred"] == [APEX_PRODUCT.address]
        assert output.read_bytes() == before
        assert state.read_bytes() == state_bytes
    finally:
        cache.close()

"""Request availability is a block observation, separate from claim capacity."""

import datetime
import runpy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from web3.exceptions import BadFunctionCallOutput

from eth_defi.erc_4626.scan import _fetch_activity_status
from eth_defi.erc_4626.vault_protocol.gains import vault as gains_module
from eth_defi.erc_4626.vault_protocol.gains.vault import BASE_GTRADE_USDC, GainsVault
from eth_defi.erc_4626.vault_protocol.plutus.vault import PLUTUS_HEDGE_V2_BLOCK, PlutusHistoricalReader, PlutusVault
from eth_defi.vault.base import REDEMPTION_CLOSED_BY_ADMIN, REDEMPTION_CLOSED_EPOCH_WINDOW, VaultSpec
from eth_defi.vault_protocols.migration import discover_migrations

HEDGE = VaultSpec(42161, "0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a")


@pytest.mark.parametrize(("paused", "status"), [(False, "open"), (True, "closed"), (None, "unknown")])
def test_plutus_scan_reads_pause_state_at_block(paused: bool | None, status: str) -> None:
    """Publish the request flag from the pause ABI even when maxRedeem is zero."""
    web3 = MagicMock()
    block = 523705861
    web3.eth.get_block.return_value = {"number": block, "timestamp": 1794528000}
    vault = PlutusVault(web3, HEDGE)
    contract = MagicMock()
    vault.__dict__["vault_contract"] = contract
    contract.functions.maxRedeem.return_value.call.return_value = 0
    pause_read = contract.functions.withdrawalsPaused.return_value.call
    if paused is None:
        pause_read.side_effect = BadFunctionCallOutput("Unreadable pause state")
    else:
        pause_read.return_value = paused

    row = _fetch_activity_status(vault, Decimal(213_000), block)
    pause_read.assert_called_once_with(block_identifier=block)
    contract.functions.maxRedeem.assert_not_called()
    assert row["_redemption_request_observation"] == {
        "status": status,
        "block_number": block,
        "observed_at": "2026-11-13T00:00:00Z",
    }
    assert row["_redemption_closed_reason"] == (REDEMPTION_CLOSED_BY_ADMIN if paused else None)


@pytest.mark.parametrize(
    ("block", "pause_word", "expected_open", "expected_cap"),
    [
        (PLUTUS_HEDGE_V2_BLOCK - 1, None, False, Decimal(0)),
        (PLUTUS_HEDGE_V2_BLOCK, 0, True, None),
        (PLUTUS_HEDGE_V2_BLOCK, 1, False, None),
        (PLUTUS_HEDGE_V2_BLOCK, None, None, None),
    ],
)
def test_plutus_historical_request_gate_changes_at_upgrade(
    block: int,
    pause_word: int | None,
    expected_open: bool | None,
    expected_cap: Decimal | None,
) -> None:
    """Historical V2 opens requests without adding an immediate withdrawal cap."""
    vault = PlutusVault(MagicMock(), HEDGE)
    vault.__dict__["share_token"] = SimpleNamespace(convert_to_decimals=lambda _: Decimal(0))
    reader = PlutusHistoricalReader(vault, stateful=False)
    reader.process_core_erc_4626_result = MagicMock(return_value=(None, None, None, [], None))
    reader.dictify_multicall_results = MagicMock(
        return_value={
            "maxRedeem": SimpleNamespace(success=True, result=bytes(32)),
            "withdrawalsPaused": (SimpleNamespace(success=True, result=pause_word.to_bytes(32)) if pause_word is not None else None),
        }
    )

    row = reader.process_result(block, datetime.datetime(2026, 7, 9), [])
    assert row.redemption_open is expected_open
    assert row.max_redeem == expected_cap


@pytest.mark.parametrize(
    ("count", "epoch_elapsed", "every", "status", "reopens"),
    [
        (4, 3 * 86400 + 50, 21600, "closed", False),
        (1, 2 * 86400 + 250, 21600, "closed", True),
        (1, 2 * 86400 + 250, 0, "closed", False),
        (1, 2 * 86400 + 250, None, "closed", False),
        (0, 86400, 21600, "open", False),
        (None, 86400, 21600, "unknown", False),
    ],
)
def test_base_gtrade_reports_request_window_and_nominal_cycle(
    monkeypatch: pytest.MonkeyPatch,
    count: int | None,
    epoch_elapsed: int,
    every: int | None,
    status: str,
    reopens: bool,
) -> None:
    """Retain a known closure when a timing estimate has expired or cannot be read."""
    web3 = MagicMock()
    block = 56688126
    epoch_start = 1794245726
    web3.eth.get_block.return_value = {"number": block, "timestamp": epoch_start + epoch_elapsed}
    vault = GainsVault(web3, BASE_GTRADE_USDC)
    contract = MagicMock()
    vault.__dict__["vault_contract"] = contract
    contract.functions.currentEpochStart.return_value.call.return_value = epoch_start
    feed = MagicMock()
    monkeypatch.setattr(gains_module, "get_deployed_contract", lambda *_: feed)
    counter = feed.functions.nextEpochValuesRequestCount.return_value.call
    if count is None:
        counter.side_effect = BadFunctionCallOutput("Unreadable request counter")
    else:
        counter.return_value = count
    feed.functions.requestsStart.return_value.call.return_value = 172800
    feed.functions.requestsCount.return_value.call.return_value = 4
    interval = feed.functions.requestsEvery.return_value.call
    if every is None:
        interval.side_effect = BadFunctionCallOutput("Unreadable epoch timing")
    else:
        interval.return_value = every

    row = _fetch_activity_status(vault, Decimal(784_000), block)
    counter.assert_called_once_with(block_identifier=block)
    observed = row["_redemption_request_observation"]
    assert observed["block_number"] == block
    assert observed["status"] == status
    assert observed["nominal_epoch_seconds"] == (259200 if every and count is not None else None)
    assert (observed["unlock_epochs_min"], observed["unlock_epochs_max"]) == (1, 3)
    assert row["_redemption_closed_reason"] == (REDEMPTION_CLOSED_EPOCH_WINDOW if status == "closed" else None)
    if reopens:
        expected = datetime.datetime.fromtimestamp(epoch_start + 259200, datetime.UTC).replace(tzinfo=None)
        assert row["_redemption_next_open"] == expected
    else:
        assert row["_redemption_next_open"] is None


def test_plutus_repair_preserves_other_price_rows(tmp_path: Path) -> None:
    """Resolve the packaged migration and stage only verified V2 rows."""
    spec = next(spec for spec in discover_migrations() if spec.number == 108)
    stage = runpy.run_path(str(spec.path))["stage_plutus_history"]
    original = pa.table(
        {
            "chain": [42161, 42161, 42161, 8453],
            "address": [HEDGE.vault_address] * 4,
            "block_number": pa.array([PLUTUS_HEDGE_V2_BLOCK - 1, PLUTUS_HEDGE_V2_BLOCK, PLUTUS_HEDGE_V2_BLOCK + 1, PLUTUS_HEDGE_V2_BLOCK], type=pa.uint64()),
            "redemption_open": ["false"] * 4,
            "max_redeem": [0.0] * 4,
            "share_price": [1.0, 1.25, 1.5, 2.0],
        }
    )
    source, destination = tmp_path / "original.parquet", tmp_path / "corrected.parquet"
    pq.write_table(original, source)

    assert stage(source, destination, {PLUTUS_HEDGE_V2_BLOCK: True, PLUTUS_HEDGE_V2_BLOCK + 1: False}) == 2
    updated = pq.read_table(destination)
    assert updated["redemption_open"].to_pylist() == ["false", "true", "false", "false"]
    assert updated["max_redeem"].to_pylist() == [0.0, None, None, 0.0]
    assert updated["share_price"] == original["share_price"]
    assert pq.read_table(source) == original

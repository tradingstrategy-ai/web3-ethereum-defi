"""Preserve price history while correcting the reviewed Upshift liquidity field."""

import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols.repairs.price_backfill import _assert_rows_unchanged
from eth_defi.vault_protocols.repairs.upshift import TARGETS, refresh_upshift_metadata, stage_upshift_liquidity_correction


def test_upshift_liquidity_correction_preserves_history(tmp_path: Path) -> None:
    """Preserve multiple row batches and make the narrow correction idempotent.

    1. Write target and unrelated histories across several row batches.
    2. Clear only the reviewed vaults' mislabelled liquidity values.
    3. Repeat the correction and prove prices, row order and unrelated values survive.
    """
    # 1. Write target and unrelated histories across several row batches.
    count = 200_003
    addresses = [TARGETS[0].vault_address if i % 2 else "0x0000000000000000000000000000000000000001" for i in range(count)]
    before = pa.table({"chain": pa.array([None] + [TARGETS[0].chain_id] * (count - 1), type=pa.uint32()), "address": addresses, "block_number": range(count), "share_price": [1.25] * count, "utilisation": [float("nan")] * count, "available_liquidity": [123.0] * count})
    source, destination, repeated = (tmp_path / name for name in ("before.parquet", "after.parquet", "repeated.parquet"))
    pq.write_table(before, source)
    # 2. Clear only the reviewed vaults' mislabelled liquidity values.
    assert stage_upshift_liquidity_correction(source, destination) == count // 2
    after = pq.read_table(destination)
    assert after.num_rows == count
    _assert_rows_unchanged(before.drop(["available_liquidity"]), after.drop(["available_liquidity"]), "test")
    assert after["available_liquidity"][0].as_py() == 123.0
    assert after["available_liquidity"][1].as_py() is None
    # 3. Repeat the correction and prove prices, row order and unrelated values survive.
    assert stage_upshift_liquidity_correction(destination, repeated) == 0
    _assert_rows_unchanged(after, pq.read_table(repeated), "idempotent correction")


def test_upshift_metadata_refresh_stages_before_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve unrelated metadata and refuse partial updates after an RPC failure.

    1. Report absent reviewed rows without inventing discoveries.
    2. Refresh two existing rows through supplied provider boundaries.
    3. Fail the second read and prove neither input row was partly updated.
    4. Refuse an unresolved admission policy without publishing partial data.
    """
    # 1. Report absent reviewed rows without inventing discoveries.
    empty = VaultDatabase(rows={})
    assert refresh_upshift_metadata(empty)["missing"] == [spec.as_string_id() for spec in TARGETS]
    # Mock only RPC-owned observations; staging and permission classification are real.
    provider = MagicMock()
    provider.eth.chain_id = 1
    vault = MagicMock()
    vault.is_whitelisted_deposit.return_value = False
    vault.get_deposit_manager_capability.return_value.as_initial_public_schema.return_value = {"can_deposit": True, "can_redeem": True}
    vault.get_estimated_lock_up.return_value = datetime.timedelta(days=3)
    vault.get_withdrawal_period.return_value = None
    vault.fetch_available_liquidity.return_value = Decimal(100)
    monkeypatch.setattr("eth_defi.vault_protocols.repairs.upshift.UpshiftVault", lambda *args, **kwargs: vault)
    rows = {}
    for spec in TARGETS[:2]:
        detection = MagicMock()
        detection.get_spec.return_value = spec
        detection.features = {ERC4626Feature.upshift_like}
        rows[spec] = {"_detection_data": detection, "unrelated": "preserve"}
    database = VaultDatabase(rows=rows)
    # 2. Refresh two existing rows through supplied provider boundaries.
    assert refresh_upshift_metadata(database, lambda chain: provider)["changed_rows"] == 2
    assert all(row["unrelated"] == "preserve" for row in rows.values())
    assert refresh_upshift_metadata(database, lambda chain: provider)["changed_rows"] == 0
    # 3. Fail the second read and prove neither input row was partly updated.
    for row in rows.values():
        row.pop("_deposit_manager")
    vault.fetch_available_liquidity.side_effect = [Decimal(100), ValueError("RPC unavailable")]
    with pytest.raises(ValueError, match="RPC unavailable"):
        refresh_upshift_metadata(database, lambda chain: provider)
    assert all("_deposit_manager" not in row for row in rows.values())
    # 4. Refuse an unresolved admission policy without publishing partial data.
    vault.fetch_available_liquidity.side_effect = None
    vault.is_whitelisted_deposit.side_effect = [False, ExtraValueError("RPC unavailable")]
    with pytest.raises(ValueError, match="Unresolved Upshift deposit permission"):
        refresh_upshift_metadata(database, lambda chain: provider)
    assert all("_deposit_manager" not in row for row in rows.values())

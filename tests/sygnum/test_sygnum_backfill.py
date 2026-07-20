"""Regression tests for the address-scoped Sygnum migration helper."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from eth_defi.tokenised_fund.sygnum import backfill
from eth_defi.tokenised_fund.sygnum.constants import FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK
from eth_defi.vault.base import VaultHistoricalRead


def test_sygnum_backfill_defaults_to_filq_a_and_accepts_filq_d() -> None:
    """Keep both independently verified FILQ share classes address-scoped."""

    lead = backfill.create_lead()
    detection = backfill.create_detection()
    assert lead.address == FILQ_A_ETHEREUM_ADDRESS
    assert lead.first_seen_at_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK
    assert detection.address == FILQ_A_ETHEREUM_ADDRESS
    assert detection.first_seen_at_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK

    distributing_lead = backfill.create_lead(FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK)
    distributing_detection = backfill.create_detection(FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK)
    assert distributing_lead.address == FILQ_D_ETHEREUM_ADDRESS
    assert distributing_detection.address == FILQ_D_ETHEREUM_ADDRESS


def create_history_read(address: str, block_number: int, share_price: Decimal) -> VaultHistoricalRead:
    """Create a minimal raw price row for Parquet replacement tests.

    :param address: Vault identifier.
    :param block_number: Row block number.
    :param share_price: Row NAV/share.
    :return: Scanner-compatible historical read.
    """

    vault = SimpleNamespace(chain_id=1, address=address, vault_address=address)
    return VaultHistoricalRead(
        vault=vault,
        block_number=block_number,
        timestamp=datetime.datetime(2026, 5, 6, tzinfo=datetime.UTC).replace(tzinfo=None),
        share_price=share_price,
        total_assets=share_price * Decimal(100),
        total_supply=Decimal(100),
        performance_fee=None,
        management_fee=None,
        errors=None,
    )


def test_write_filq_historical_reads_preserves_unrelated_rows(tmp_path) -> None:
    """Replace only FILQ rows at and after the selected report boundary."""

    expected_inserted = 2
    expected_total_rows = 3
    expected_filq_a_nav = 100.0085
    path = tmp_path / "vault-prices.parquet"
    unrelated_address = "0x0000000000000000000000000000000000000001"
    old_rows = [
        create_history_read(unrelated_address, 25_000_000, Decimal(2)),
        create_history_read(FILQ_A_ETHEREUM_ADDRESS, 25_038_639, Decimal(99)),
    ]
    old_table = pa.Table.from_pylist([row.export() for row in old_rows], schema=VaultHistoricalRead.to_pyarrow_schema())
    VaultHistoricalRead.write_uncleaned_arrow_table(old_table, path)

    new_rows = [
        create_history_read(FILQ_A_ETHEREUM_ADDRESS, 25_038_639, Decimal("100.0085")),
        create_history_read(FILQ_D_ETHEREUM_ADDRESS, 25_139_026, Decimal(1)),
    ]
    deleted, inserted = backfill.write_filq_historical_reads(path, new_rows, start_block=25_038_639)

    assert deleted == 1
    assert inserted == expected_inserted
    result = pq.read_table(path).to_pandas()
    assert len(result) == expected_total_rows
    assert set(result["address"]) == {unrelated_address, FILQ_A_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_ADDRESS}
    filq_a = result[result["address"] == FILQ_A_ETHEREUM_ADDRESS].iloc[0]
    assert filq_a["share_price"] == expected_filq_a_nav

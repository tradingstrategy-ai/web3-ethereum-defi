"""Test historical scan operator summaries."""

from pathlib import Path

from eth_defi.vault.historical import pformat_scan_result


def test_pformat_scan_result_supports_stateless_scan() -> None:
    """A stateless scan reports zero reader states instead of raising."""

    result = {
        "existing": True,
        "chain_id": 1,
        "rows_written": 3,
        "rows_deleted": 2,
        "existing_row_count": 10,
        "output_fname": Path("prices.parquet"),
        "file_size": 123,
        "chunks_done": 1,
        "start_block": 100,
        "end_block": 200,
        "rows_written_by_vault": {},
        "price_rows_written_by_vault": {},
        "reader_states": None,
    }

    summary = pformat_scan_result(result)

    assert "reader_state_count=0" in summary

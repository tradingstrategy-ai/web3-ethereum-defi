"""Equity charts preserve observed source bounds and unknown bootstrap TVL."""

from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.antarctic.charts import create_antarctic_figure, fetch_antarctic_chart_series
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_DEPLOYMENTS
from eth_defi.erc_4626.vault_protocol.antarctic.historical_context import AntarcticHistoricalContextStore
from eth_defi.testing.antarctic import RecordedAntarcticProvider, create_antarctic_test_metadata, load_antarctic_settlements
from eth_defi.token import TokenDiskCache

FIXTURES = Path(__file__).parent / "fixtures"


def test_antarctic_equity_and_tvl_chart_data(tmp_path: Path) -> None:
    """Render the same actual subscriptions exposed by the historical reader.

    Sparse timestamps and missing bootstrap assets remain intact. Equity starts
    at 100 rather than interpreting subscription cash inflows as investment PnL.
    The chart does not extend to the scan cutoff or add redemption observations.

    :param tmp_path: Isolated context and token metadata cache.
    :return: None.
    """
    records = load_antarctic_settlements(FIXTURES / "antarctic-settlements.json")
    web3 = Web3(RecordedAntarcticProvider(FIXTURES / "antarctic-rpc.json"))
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    context = tmp_path / "context.duckdb"
    start, end = min(r.block_number for r in records), max(r.block_number for r in records) + 1000
    try:
        create_antarctic_test_metadata(web3, cache)
        with AntarcticHistoricalContextStore(context) as store:
            for deployment in ANTARCTIC_DEPLOYMENTS:
                store.replace_range(deployment.address, start, end, iter(r for r in records if r.pool_address == deployment.address))
        for deployment, frame in fetch_antarctic_chart_series(web3, context, cache, end):
            observations = [r for r in records if r.pool_address == deployment.address and r.kind == "AddLiquidity"]
            expected_blocks = sorted({r.block_number for r in observations})
            last = max(observations, key=lambda r: (r.block_number, r.log_index))
            assert frame["block_number"].tolist() == expected_blocks
            assert frame["equity_index"].iloc[0] == 100  # noqa: PLR2004
            assert frame["total_assets"].isna().iloc[0]
            assert frame["share_price"].iloc[-1] == pytest.approx(last.raw_usdt / last.raw_shares * 10**12)
            assert frame["total_assets"].iloc[-1] == pytest.approx(last.raw_tvl / 10**6)
            figure = create_antarctic_figure(frame, deployment.product)
            assert list(figure.data[0].x) == frame["timestamp"].tolist()
            assert len(figure.data[1].y) == len(expected_blocks)
            assert not figure.data[1].connectgaps
            assert "staking rewards excluded" in figure.layout.annotations[-1].text
    finally:
        cache.close()

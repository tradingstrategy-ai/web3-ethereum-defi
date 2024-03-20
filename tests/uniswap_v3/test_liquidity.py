"""Test Uniswap v3 liquidity."""
import pytest

from eth_defi.uniswap_v3.liquidity import estimate_liquidity_depth_at_block

#  gql.transport.exceptions.TransportQueryError: Error while fetching schema: {'message': 'indexing_error'}

# TheGraph is broken
@pytest.mark.skip(reason=" gql.transport.exceptions.TransportQueryError: Error while fetching schema: {'message': 'indexing_error'}")
def test_liquidity_depth_at_block():
    # MKR/ETH 0.3%
    pool_address = "0xe8c6c9227491c0a8156a0106a0204d881bb7e531"
    depths = estimate_liquidity_depth_at_block(pool_address, 14722452)

    assert len(depths) == 12
    assert len(depths[0]) == 3
    assert depths[0][0] == -5
    assert depths[0][2] == pytest.approx(18.789228638418738)
    assert depths[-1][0] == 5

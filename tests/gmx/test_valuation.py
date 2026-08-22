"""Tests for :mod:`eth_defi.gmx.valuation`.

The main end-to-end NAV integration test lives beside its fixture in
``tests/gmx/lagoon/test_gmx_close_pnl_token.py::test_fetch_gmx_total_equity_end_to_end``
rather than here, since it depends on that module's Lagoon-Safe fork
environment and open/close helpers.

This module covers the one failure mode that integration test cannot: an
unpriceable reserve token must raise loudly rather than silently contribute
zero to NAV.
"""

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.gmx.types import OraclePriceMap
from eth_defi.gmx.valuation import _oracle_price_tuple, fetch_gmx_total_equity  # noqa: PLC2701
from eth_defi.token import create_token, fetch_erc20_details


def test_oracle_price_tuple_reads_raw_gmx_prices() -> None:
    """``_oracle_price_tuple`` reads a token's raw ``(min, max)`` price as integers.

    Characterisation test for the untyped oracle-price boundary
    :func:`~eth_defi.gmx.valuation._oracle_price_tuple` reads from -- see
    :class:`~eth_defi.gmx.types.OraclePriceMap`. No RPC access required.
    """
    token = HexAddress("0x0000000000000000000000000000000000000001")
    prices: OraclePriceMap = {token: {"minPriceFull": "10", "maxPriceFull": "12"}}

    assert _oracle_price_tuple(prices, token) == (10, 12)


def test_reserves_unpriceable_token_raises(
    web3_arbitrum_fork: Web3,
    test_address: HexAddress,
    chain_name,
):
    """A non-stablecoin reserve with no GMX oracle price raises loudly.

    The one failure mode the happy-path integration test above cannot cover:
    an unpriceable balance must be a loud error, not a legitimate zero.
    """
    if chain_name != "arbitrum":
        pytest.skip("Reserve pricing test only targets Arbitrum")

    mock_token_contract = create_token(
        web3_arbitrum_fork,
        deployer=test_address,
        name="Definitely Not Priced By GMX",
        symbol="NOPRICE",
        supply=1_000 * 10**18,
    )
    mock_token = fetch_erc20_details(web3_arbitrum_fork, mock_token_contract.address)
    assert not mock_token.is_stablecoin_like()

    with pytest.raises(ValueError, match="No oracle price available"):
        fetch_gmx_total_equity(
            web3=web3_arbitrum_fork,
            account=test_address,
            reserve_tokens=[mock_token],
            block_identifier="latest",
            chain="arbitrum",
            include_native_eth=False,
        )

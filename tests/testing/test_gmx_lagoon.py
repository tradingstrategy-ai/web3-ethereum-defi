"""Verify private-fork position reads retain the real RPC reader path."""

from unittest.mock import Mock

from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.testing.gmx_lagoon import gmx_fork_position_reads


def test_fork_position_reads_use_rpc_and_restore_tiers() -> None:
    """Skip public indexers only while exercising local fork transactions.

    The real reader dispatch must reach RPC after empty public-tier results,
    then restore both original methods when the context exits.

    :return:
        None; assertions verify dispatch and method restoration.
    """
    rest = GetOpenPositions._get_data_via_rest_api
    graphql = GetOpenPositions._get_data_via_graphql
    reader = object.__new__(GetOpenPositions)
    expected = {"ETH": {"is_long": True}}
    reader._get_data_via_rpc = Mock(return_value=expected)
    address = "0x0000000000000000000000000000000000000001"
    with gmx_fork_position_reads():
        assert reader.get_data(address) == expected
        reader._get_data_via_rpc.assert_called_once_with(address)
    assert GetOpenPositions._get_data_via_rest_api is rest
    assert GetOpenPositions._get_data_via_graphql is graphql

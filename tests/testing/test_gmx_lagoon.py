"""Verify isolated fork readers and per-test token metadata reuse."""

from unittest.mock import Mock, patch

import pytest

from eth_defi.gmx import contracts
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.testing.gmx_lagoon import gmx_fork_position_reads, gmx_fork_token_metadata


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


def test_fork_metadata_reuses_live_response_without_leaking_mutations() -> None:
    """Keep token metadata stable within one test and fresh in the next context.

    Exercise the real public mapping helper while counting provider fetches.
    Mutating a caller's result must not alter another reader or a later test.

    :return:
        None; assertions verify request count, copy isolation and restoration.
    """
    response = {"token": {"symbol": "ETH", "decimals": 18}}
    with patch.object(contracts, "_fetch_tokens_from_gmx_api", return_value=response) as fetch:
        with gmx_fork_token_metadata():
            first = contracts.get_tokens_metadata_dict("arbitrum")
            first["token"]["decimals"] = 6
            assert contracts.get_tokens_metadata_dict("arbitrum")["token"]["decimals"] == 18
            assert fetch.call_count == 1
        assert contracts._fetch_tokens_from_gmx_api is fetch
        with gmx_fork_token_metadata():
            assert contracts.get_tokens_metadata_dict("arbitrum")["token"]["decimals"] == 18
        assert fetch.call_count == 2


def test_fork_metadata_retries_failures_and_restores_after_exception() -> None:
    """Propagate a provider failure and restore the reader after test failure.

    A transient failed fetch must not poison the per-test cache or leave the
    patched reader installed when the surrounding test raises.

    :return:
        None; assertions verify retry and exceptional cleanup.
    """
    response = {"token": {"symbol": "ETH", "decimals": 18}}
    with patch.object(contracts, "_fetch_tokens_from_gmx_api", side_effect=[ValueError("provider unavailable"), response]) as fetch:
        with pytest.raises(RuntimeError, match="test failed"):
            with gmx_fork_token_metadata():
                with pytest.raises(ValueError, match="provider unavailable"):
                    contracts.get_tokens_metadata_dict("arbitrum")
                assert contracts.get_tokens_metadata_dict("arbitrum") == response
                assert contracts.get_tokens_metadata_dict("arbitrum") == response
                assert fetch.call_count == 2
                raise RuntimeError("test failed")
        assert contracts._fetch_tokens_from_gmx_api is fetch

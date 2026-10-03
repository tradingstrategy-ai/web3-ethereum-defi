"""Failure-path coverage for strict EVM snapshot restoration."""

from unittest.mock import Mock

import pytest

from eth_defi.testing import evm_snapshot_fixture


def test_strict_revert_failure_stops_state_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed revert must fail teardown instead of reusing contaminated state."""
    web3 = Mock()
    web3.provider.make_request.side_effect = [{"result": "0x1"}, {"result": False}]
    monkeypatch.setattr(evm_snapshot_fixture, "Web3", Mock(return_value=web3))
    isolation = evm_snapshot_fixture.evm_snapshot_revert("http://localhost:8545", strict=True)
    next(isolation)
    with pytest.raises(RuntimeError, match="evm_revert failed"):
        next(isolation)

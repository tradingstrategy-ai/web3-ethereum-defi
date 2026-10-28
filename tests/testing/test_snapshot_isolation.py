"""Failure-path coverage for strict EVM snapshot restoration."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.testing import evm_snapshot_fixture


def test_strict_revert_failure_raises_teardown_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed revert must raise an explicit teardown error.

    Return a failed evm_revert response from the provider and require an
    explicit teardown error; automatic process recovery is outside this helper.

    :param monkeypatch:
        Restore modified environment or provider objects after the test.

    :return:
        None; assertions validate the behaviour.
    """
    web3 = Mock()
    web3.provider.make_request.side_effect = [{"result": "0x1"}, {"result": False}]
    monkeypatch.setattr(evm_snapshot_fixture, "Web3", Mock(return_value=web3))
    isolation = evm_snapshot_fixture.evm_snapshot_revert("http://localhost:8545", strict=True)
    next(isolation)
    with pytest.raises(RuntimeError, match="evm_revert failed"):
        next(isolation)


def test_snapshot_provider_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot and revert calls cannot multiply a stalled fork's timeout.

    Keep the existing request budget while disabling Web3's implicit retries.

    :param monkeypatch: Restore replaced provider constructors after the test.
    :return: None.
    """
    web3 = Mock()
    web3.provider.make_request.side_effect = [{"result": "0x1"}, {"result": True}]
    provider = Mock()
    monkeypatch.setattr(evm_snapshot_fixture, "create_http_provider", provider)
    monkeypatch.setattr(evm_snapshot_fixture, "Web3", Mock(return_value=web3))
    isolation = evm_snapshot_fixture.evm_snapshot_revert("http://localhost:8545")
    next(isolation)
    with pytest.raises(StopIteration):
        next(isolation)
    assert provider.call_args.kwargs["exception_retry_configuration"] is None
    assert provider.call_args.kwargs["request_kwargs"]["timeout"] == evm_snapshot_fixture.SNAPSHOT_HTTP_TIMEOUT


@pytest.mark.parametrize("strict", [False, True])
def test_unresponsive_fork_is_not_reverted(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, *, strict: bool) -> None:
    """Avoid queueing a long revert behind a wedged Anvil request.

    Strict users receive a teardown error; the default warns so the pool can
    discard the fork on its next reuse check.

    :param monkeypatch: Restore replaced RPC helpers after the test.
    :param caplog: Capture the explicit default-mode warning.
    :param strict: Whether lost isolation must raise an error.
    :return: None.
    """
    fork = Mock(spec=AnvilLaunch, json_rpc_url="http://localhost:8545")
    web3 = Mock()
    web3.provider.make_request.return_value = {"result": "0x1"}
    monkeypatch.setattr(evm_snapshot_fixture, "Web3", Mock(return_value=web3))
    monkeypatch.setattr(evm_snapshot_fixture, "is_fork_alive", Mock(return_value=False))
    isolation = evm_snapshot_fixture.evm_snapshot_revert(fork, strict=strict)
    next(isolation)
    expected = RuntimeError if strict else StopIteration
    with pytest.raises(expected):
        next(isolation)
    assert web3.provider.make_request.call_count == 1
    if not strict:
        assert "unresponsive" in caplog.text


def test_unresponsive_revert_preserves_original_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed liveness probe must not suppress the test body's exception.

    Exercise the generator through its context-manager protocol to check that
    non-strict teardown keeps the original failure visible.

    :param monkeypatch: Restore replaced RPC helpers after the test.
    :return: None.
    """
    fork = Mock(spec=AnvilLaunch, json_rpc_url="http://localhost:8545")
    web3 = Mock()
    web3.provider.make_request.return_value = {"result": "0x1"}
    monkeypatch.setattr(evm_snapshot_fixture, "Web3", Mock(return_value=web3))
    monkeypatch.setattr(evm_snapshot_fixture, "is_fork_alive", Mock(return_value=False))
    with pytest.raises(ValueError, match="original test failure"), contextmanager(evm_snapshot_fixture.evm_snapshot_revert)(fork):
        raise ValueError("original test failure")

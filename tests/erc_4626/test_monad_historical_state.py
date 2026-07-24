"""Tests for Monad historical state retention handling."""

from types import SimpleNamespace

import pytest

from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.vault import historical

GENESIS_BLOCK = 0
MONAD_RETAINED_STATE_START_BLOCK = 600
LATEST_BLOCK = 1_000
MAX_BINARY_SEARCH_PROBES = 12
HISTORICAL_STATE_EVICTED = "Monad historical state evicted"


def _create_multicall_probe(available_from_block: int) -> tuple[SimpleNamespace, list[int]]:
    """Create a Multicall mock with a contiguous historical state window.

    :param available_from_block:
        First block at which the mock provider can execute a historical call.

    :return:
        Mock contract and the probed block numbers.
    """
    calls: list[int] = []

    def _call(*, block_identifier: int) -> int:
        """Simulate a historical Multicall execution."""
        calls.append(block_identifier)
        if block_identifier < available_from_block:
            raise ProbablyNodeHasNoBlock(HISTORICAL_STATE_EVICTED)
        return block_identifier

    def _get_block_number() -> SimpleNamespace:
        """Create the bound ``getBlockNumber`` call mock."""
        return SimpleNamespace(call=_call)

    multicall = SimpleNamespace(
        functions=SimpleNamespace(getBlockNumber=_get_block_number),
    )
    return multicall, calls


def test_fetch_monad_historical_state_start_block_clips_to_provider_window(monkeypatch) -> None:
    """Find the first readable block without attempting an old price scan."""
    multicall, calls = _create_multicall_probe(available_from_block=MONAD_RETAINED_STATE_START_BLOCK)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=143))
    monkeypatch.setattr(historical, "get_multicall_contract", lambda _web3: multicall)

    start_block = historical.fetch_monad_historical_state_start_block(
        web3,
        start_block=GENESIS_BLOCK,
        end_block=LATEST_BLOCK,
    )

    assert start_block == MONAD_RETAINED_STATE_START_BLOCK
    assert calls[0] == GENESIS_BLOCK
    assert calls[1] == LATEST_BLOCK
    assert len(calls) <= MAX_BINARY_SEARCH_PROBES


def test_fetch_monad_historical_state_start_block_preserves_readable_start(monkeypatch) -> None:
    """Avoid unnecessary boundary probing when all requested state is readable."""
    multicall, calls = _create_multicall_probe(available_from_block=500)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=143))
    monkeypatch.setattr(historical, "get_multicall_contract", lambda _web3: multicall)

    start_block = historical.fetch_monad_historical_state_start_block(
        web3,
        start_block=MONAD_RETAINED_STATE_START_BLOCK,
        end_block=LATEST_BLOCK,
    )

    assert start_block == MONAD_RETAINED_STATE_START_BLOCK
    assert calls == [MONAD_RETAINED_STATE_START_BLOCK]


def test_fetch_monad_historical_state_start_block_rejects_unavailable_end(monkeypatch) -> None:
    """Treat unavailable current state as a provider fault, not a retention boundary."""
    multicall, _calls = _create_multicall_probe(available_from_block=1_001)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=143))
    monkeypatch.setattr(historical, "get_multicall_contract", lambda _web3: multicall)

    with pytest.raises(RuntimeError, match="cannot read state"):
        historical.fetch_monad_historical_state_start_block(
            web3,
            start_block=GENESIS_BLOCK,
            end_block=LATEST_BLOCK,
        )

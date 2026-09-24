"""Tests for RPC capability verification."""

from unittest.mock import MagicMock, call

import pytest

from eth_defi.provider import multi_provider
from eth_defi.provider.broken_provider import verify_archive_node

LATEST_BLOCK = 100


@pytest.mark.parametrize(
    ("chain_name", "expected_blocks"),
    [
        ("Monad", [LATEST_BLOCK]),
        ("Base", [1, LATEST_BLOCK]),
    ],
)
def test_verify_archive_node_skips_genesis_state_probe_for_monad(
    monkeypatch: pytest.MonkeyPatch,
    chain_name: str,
    expected_blocks: list[int],
) -> None:
    """Monad verification must not make an impossible genesis-state request.

    Monad checks only that the endpoint can read the current state, whereas
    archive-capable chains retain the block-one verification probe.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    :param chain_name:
        Chain name supplied to the verifier.
    :param expected_blocks:
        Blocks expected in balance probes.
    """
    get_balance = MagicMock()
    web3 = MagicMock()
    web3.eth.block_number = LATEST_BLOCK
    web3.eth.get_balance = get_balance
    monkeypatch.setattr(multi_provider, "create_multi_provider_web3", lambda *_args, **_kwargs: web3)

    rpc_url, latest_block = verify_archive_node("https://rpc.example", chain_name)

    assert rpc_url == "https://rpc.example"
    assert latest_block == LATEST_BLOCK
    assert get_balance.call_args_list == [call("0x0000000000000000000000000000000000000000", block_identifier=block_number) for block_number in expected_blocks]


def test_verify_archive_node_does_not_log_rpc_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider failures expose a category and domain, never the raw URL.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    :param caplog:
        Captured verifier logs.
    :return:
        ``None``. Assertions verify that credential material is redacted.
    """
    secret = "rpc-secret-that-must-not-be-logged"

    def fail_to_connect(*_args: object, **_kwargs: object) -> None:
        """Raise an error whose message resembles a credential-bearing provider exception."""

        raise ConnectionError(f"Could not connect to https://rpc.example/v2/key?secret={secret}")

    monkeypatch.setattr(multi_provider, "create_multi_provider_web3", fail_to_connect)

    with pytest.raises(RuntimeError, match="failure_mode="):
        verify_archive_node(f"https://rpc.example/v2/key?secret={secret}", "Arc")

    assert "rpc.example" in caplog.text
    assert secret not in caplog.text

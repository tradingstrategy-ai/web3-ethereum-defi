"""Offline branch-proof regressions; no RPC, signing or chain state is required."""

import ast
import inspect
import socket
import textwrap
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eth_defi.gmx.freqtrade.sensitive_filter import patch_logging

patch_logging()

# Install log redaction before any import that can emit Freqtrade/SDK output.

from eth_defi.gmx.ccxt.exchange import GMX  # noqa: E402
from eth_defi.gmx.constants import _GAS_CRITICAL_MAX_RETRIES  # noqa: PLC2701, E402
from eth_defi.gmx.freqtrade.gmx_exchange import Gmx, GmxPreflightGasRefusal  # noqa: E402
from eth_defi.gmx.gas_monitor import GasCheckResult, GasMonitorConfig  # noqa: E402
from eth_defi.gmx.gas_refusal import (  # noqa: E402
    GasRefusalProof,
    GasRejectedOrder,
    GMXPreflightGasError,
    is_gas_rejected_result,
)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> GMX:
    """Stop at the actual pre-sign gas check with all network access prohibited."""

    def no_network(*_args: object, **_kwargs: object) -> None:
        message = "Unexpected network access"
        raise AssertionError(message)

    monkeypatch.setattr(socket.socket, "connect", no_network)
    result = GMX.__new__(GMX)
    result.wallet = SimpleNamespace(address="0x1111111111111111111111111111111111111111")
    result.wallet.sign_transaction_with_new_nonce = Mock(side_effect=AssertionError("must not sign"))
    result.web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    result._trading_paused = False
    result._gas_monitor_config = GasMonitorConfig(enabled=True, raise_on_critical=False)
    result._gas_monitor = Mock()
    result._gas_monitor.check_gas_balance.return_value = GasCheckResult(
        False,
        0,
        Decimal(0),
        2000,
        0,
        "critical",
        "test low gas",
    )
    result._send_signed_tx_with_nonce_recovery = Mock(side_effect=AssertionError("must not broadcast"))
    result.close = Mock()
    return result


@pytest.mark.parametrize("raises", [False, True])
def test_exact_branch_proof_precedes_sign_and_broadcast(api: GMX, *, raises: bool) -> None:
    """Both critical branches prove refusal before any signer or submitter runs."""
    api._gas_monitor_config.raise_on_critical = raises
    started = datetime.now(UTC)
    if raises:
        with pytest.raises(GMXPreflightGasError) as caught:
            api.create_order("ETH/USDC:USDC", "market", "sell", 1)
        assert type(caught.value) is GMXPreflightGasError
        proof = caught.value.proof
        assert proof.branch == "ccxt_critical_raise"
        assert proof.raw_result is None
    else:
        result = api.create_order("ETH/USDC:USDC", "market", "sell", 1)
        assert type(result) is GasRejectedOrder
        proof = result.proof
        assert proof.branch == "ccxt_critical_return"
        assert proof.raw_result == dict(result)
        assert is_gas_rejected_result(result)
    assert type(proof) is GasRefusalProof
    assert proof.version == 1 and proof.phase == "pre_sign_pre_submit"
    assert proof.chain_id == api.web3.eth.chain_id and proof.payer == api.wallet.address
    assert proof.pair == "ETH/USDC:USDC"
    assert started <= proof.observed_at <= datetime.now(UTC)
    assert proof.signed is False and proof.broadcast is False
    api.wallet.sign_transaction_with_new_nonce.assert_not_called()
    api._send_signed_tx_with_nonce_recovery.assert_not_called()


@pytest.mark.parametrize("filled", [None, "", "0", False, float("nan"), float("inf"), 1])
def test_gas_result_rejects_ambiguous_quantities(filled: object) -> None:
    """A result shape cannot infer missing, false or non-finite quantity as zero."""
    assert not is_gas_rejected_result({"status": "rejected", "filled": filled, "info": {"error": "insufficient_gas"}})


def test_wrapper_normalises_all_refusals_after_count_and_cooldown(api: GMX) -> None:
    """Every attempt preserves its raw result and the threshold alert fires once."""
    exchange = Gmx.__new__(Gmx)
    exchange.close = Mock()
    exchange._gas_critical_attempts = {}
    exchange._send_gas_critical_telegram_alert = Mock()
    result = api.create_order("ETH/USDC:USDC", "market", "sell", 1)
    for count in (1, 2, 3):
        refusal = exchange._normalise_gas_refusal(result.proof)
        assert type(refusal) is GmxPreflightGasRefusal
        assert refusal.proof.raw_result == dict(result)
        assert refusal.proof.failure_count == count
        assert exchange._gas_critical_attempts[result.proof.pair][0] == count
        assert (refusal.proof.retry_not_before is not None) == (count == _GAS_CRITICAL_MAX_RETRIES)
    exchange._send_gas_critical_telegram_alert.assert_called_once_with(result.proof.pair, 3)
    assert exchange.gas_retry_not_before(result.proof.pair) == refusal.proof.retry_not_before


def test_only_expected_worktree_sources_are_imported() -> None:
    """Attest that tests import this SDK checkout rather than installed packages."""
    root = Path(__file__).resolve().parents[3]
    for symbol in (GMX, Gmx, GasRefusalProof):
        path = Path(inspect.getsourcefile(symbol)).resolve()
        assert path.is_relative_to(root / "eth_defi"), path


def test_create_order_retains_stock_retry_decorator() -> None:
    """The new deadline reader must not steal the existing create-order decorator."""
    assert hasattr(Gmx.create_order, "__wrapped__")
    assert not hasattr(Gmx.gas_retry_not_before, "__wrapped__")


def test_proof_constructors_are_before_signing_boundary() -> None:
    """Only the two lower critical preflight branches construct lower proofs."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(GMX.create_order)))
    proof_lines = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "GasRefusalProof"]
    sign_lines = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "sign_transaction_with_new_nonce"]
    expected_branches = 2
    assert len(proof_lines) == expected_branches
    assert sign_lines and max(proof_lines) < min(sign_lines)

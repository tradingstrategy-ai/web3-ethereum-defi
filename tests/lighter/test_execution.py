"""Money-movement boundaries for Lighter public-pool execution."""

# ruff: noqa: PLR2004

import datetime
import json
import subprocess  # noqa: S404
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from eth_defi.compat import native_datetime_utc_now
from eth_defi.lighter import execution as ex
from eth_defi.lighter.constants import LIGHTER_API_URL, LIGHTER_ETHEREUM


@pytest.fixture
def session() -> Mock:
    """Use an Ethereum session double without live mutations."""
    return Mock(api_url=LIGHTER_API_URL, deployment=LIGHTER_ETHEREUM)


@pytest.fixture
def pool() -> ex.LighterPoolState:
    """Each raw share is worth 2.5 USDC, unrelated to USDC token decimals."""
    return ex.LighterPoolState(42, 0, Decimal(250), 100, 20, Decimal("0.1"), Decimal("0.2"), Decimal(250), False, datetime.timedelta(seconds=300), native_datetime_utc_now())


@pytest.fixture
def holding(pool: ex.LighterPoolState) -> ex.LighterPoolHolding:
    """Ten shares with 20-USDC principal and a one-USDC estimated profit fee."""
    return ex.LighterPoolHolding(pool.pool_index, 10, Decimal(20), Decimal(20), native_datetime_utc_now() - datetime.timedelta(hours=1), Decimal(25), Decimal(1), pool.observed_at)


@pytest.fixture
def account() -> dict:
    """Strict account response containing both equity and investor shares."""
    return {"index": 123, "collateral": "40", "available_balance": "40", "total_asset_value": "40", "cross_initial_margin_requirement": "0", "cross_maintenance_margin_requirement": "0", "positions": [], "shares": []}


def test_raw_share_sizing_and_full_exit(pool: ex.LighterPoolState, holding: ex.LighterPoolHolding) -> None:
    """Mint rounds down in raw shares and full burn leaves no decimal dust."""
    assert ex.prepare_pool_amount(pool, Decimal("10.99"), available_collateral=Decimal(20)) == 4
    closed = replace(pool, status=1)
    assert ex.prepare_pool_amount(closed, Decimal("25"), available_collateral=Decimal(0), holding=holding, full_exit=True) == holding.shares
    assert ex.prepare_pool_amount(closed, Decimal("7.99"), available_collateral=Decimal(0), holding=holding) == 3
    assert holding.estimated_proceeds == Decimal(24)


@pytest.mark.parametrize("failure", ["closed", "full", "collateral", "stale", "dust", "lp_limit"])
def test_mint_rejects_invalid_constraints(pool: ex.LighterPoolState, failure: str) -> None:
    """No mint is sized from insufficient, stale or unavailable constraints."""
    available = Decimal(20)
    amount = Decimal(10)
    if failure == "closed":
        pool = replace(pool, status=1)
    if failure == "full":
        pool = replace(pool, operator_shares=10)
    if failure == "collateral":
        available = Decimal(9)
    if failure == "stale":
        pool = replace(pool, observed_at=pool.observed_at - datetime.timedelta(minutes=1))
    if failure == "dust":
        amount = Decimal("0.01")
    if failure == "lp_limit":
        pool = replace(pool, is_llp=True)
    with pytest.raises(AssertionError):
        ex.prepare_pool_amount(pool, amount, available_collateral=available)


def test_llp_and_redemption_constraints(pool: ex.LighterPoolState, holding: ex.LighterPoolHolding) -> None:
    """LLP needs verified staking capacity and exits respect cooldown/liquidity."""
    llp = replace(pool, is_llp=True)
    assert ex.prepare_pool_amount(llp, Decimal(10), available_collateral=Decimal(20), llp_deposit_limit=Decimal(10)) == 4
    with pytest.raises(AssertionError, match="staking limit"):
        ex.prepare_pool_amount(llp, Decimal(10), available_collateral=Decimal(20), llp_deposit_limit=Decimal(9))
    with pytest.raises(AssertionError, match="cooldown"):
        ex.prepare_pool_amount(llp, Decimal(10), available_collateral=Decimal(0), holding=replace(holding, entered_at=native_datetime_utc_now()))
    with pytest.raises(AssertionError, match="redeemable"):
        ex.prepare_pool_amount(replace(pool, available_balance=Decimal(1)), Decimal(10), available_collateral=Decimal(0), holding=holding)
    with pytest.raises(AssertionError, match="Insufficient investor"):
        ex.prepare_pool_amount(pool, Decimal(100), available_collateral=Decimal(0), holding=holding)


@pytest.mark.parametrize("missing", ["total_asset_value", "collateral", "available_balance", "shares", "positions"])
def test_execution_rejects_missing_account_data(monkeypatch: pytest.MonkeyPatch, session: Mock, account: dict, missing: str) -> None:
    """Legacy parser fallbacks cannot silently value the execution account."""
    account.pop(missing)
    monkeypatch.setattr(ex, "fetch_lighter_account_by_index", lambda *_: account)
    with pytest.raises((ValueError, AssertionError)):
        ex.fetch_execution_account(session, 123)


def test_holdings_use_investor_shares_and_current_value(monkeypatch: pytest.MonkeyPatch, session: Mock, account: dict, pool: ex.LighterPoolState) -> None:
    """Principal and operator fees remain separate from account equity."""
    account["shares"] = [{"public_pool_index": 42, "shares_amount": 10, "principal_amount": "20", "entry_usdc": "19", "entry_timestamp": 1700000000}]
    monkeypatch.setattr(ex, "fetch_lighter_account_by_index", lambda *_: account)
    monkeypatch.setattr(ex, "fetch_pool_state", lambda *_: pool)
    (row,) = ex.fetch_pool_holdings(session, 123)
    assert row.gross_value == Decimal(25) and row.estimated_fee == Decimal(1)
    assert row.principal == Decimal(20) and row.entry_usdc == Decimal(19)


def test_current_pool_converts_operator_fee_percentage(monkeypatch: pytest.MonkeyPatch, session: Mock, account: dict) -> None:
    """A real API fee such as ``20`` means twenty percent, not a fraction."""
    account["pool_info"] = {"status": 0, "total_shares": 100, "operator_shares": 20, "min_operator_share_rate": "10", "operator_fee": "20"}
    monkeypatch.setattr(ex, "fetch_lighter_account_by_index", lambda *_: account)
    monkeypatch.setattr(ex, "fetch_system_config", lambda *_: {"liquidity_pool_index": 99, "liquidity_pool_cooldown_period": 300000})
    state = ex.fetch_pool_state(session, 42)
    assert state.operator_fee == Decimal("0.2")
    assert state.min_operator_share_rate == Decimal("0.1")
    account["pool_info"]["total_shares"] = 100.5
    with pytest.raises(AssertionError, match="integers"):
        ex.fetch_pool_state(session, 42)


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch, session: Mock) -> ex.LighterPoolExecutor:
    """Use an injected synchronous signer and durable recorder spy."""
    signer = Mock()

    def sign(pool: int, amount: int, *, nonce: int, api_key_index: int) -> tuple:
        return (18, json.dumps({"AccountIndex": 123, "ApiKeyIndex": api_key_index, "Nonce": nonce, "PublicPoolIndex": pool, "ShareAmount": amount}), "0xabc", None)

    signer.sign_mint_shares.side_effect = sign
    signer.sign_burn_shares.side_effect = sign
    signer.sign_withdraw.return_value = (12, json.dumps({"FromAccountIndex": 123, "ApiKeyIndex": 4, "Nonce": 7, "AssetIndex": 3, "RouteType": 0, "Amount": 1000000}), "0xaaa", None)
    monkeypatch.setattr(ex, "fetch_next_nonce", lambda *_: 7)
    return ex.LighterPoolExecutor(session, ex.LighterAccountIdentity(123), signer, Mock())


def receipt(operation: ex.LighterOperation, status: int = 2) -> dict:
    """Return the pinned SDK's flat EnrichedTx response shape."""
    return {"code": 200, "hash": operation.tx_hash, "type": operation.tx_type, "account_index": 123, "api_key_index": 4, "nonce": operation.nonce, "status": status, "executed_at": 1700000000 if status == 2 else 0}


def test_prepare_is_persisted_before_submission(executor: ex.LighterPoolExecutor) -> None:
    """The signer receives exact integers and an explicit tracked nonce."""
    operation = executor.prepare("mint_shares", 4, 42)
    executor.signer.sign_mint_shares.assert_called_once_with(42, 4, nonce=7, api_key_index=4)
    executor.persist.assert_called_once_with(operation)
    with pytest.raises(AssertionError, match="Reconcile"):
        executor.prepare("mint_shares", 4, 42)
    loaded = ex.LighterOperation.from_dict(operation.as_dict())
    assert loaded == operation and loaded.identity.signing_chain_id == 304
    assert "signed" not in repr(operation)


def test_acceptance_waits_for_execution(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor) -> None:
    """API acceptance alone is submitted; a matching executed receipt confirms."""
    op = executor.prepare("mint_shares", 4, 42)
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"code": 200, "tx_hash": op.tx_hash}
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response
    monkeypatch.setattr(ex.requests, "Session", lambda: client)
    executor.submit(op)
    assert client.post.call_args.kwargs["allow_redirects"] is False
    assert op.stage == ex.LighterOperationStage.submitted
    monkeypatch.setattr(ex, "fetch_operation_status", lambda *_: receipt(op))
    executor.reconcile(op)
    assert op.stage == ex.LighterOperationStage.confirmed
    next_op = executor.prepare("burn_shares", 2, 42)
    assert next_op.nonce == op.nonce + 1


@pytest.mark.parametrize("failure", ["transport", "rejected", "wrong_hash"])
def test_submission_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor, failure: str) -> None:
    """An uncertain send blocks another signature and never replays the POST."""
    op = executor.prepare("mint_shares", 4, 42)
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value.json.return_value = {"code": 400 if failure == "rejected" else 200, "tx_hash": "other"}
    client.post.return_value.status_code = 200
    if failure == "transport":
        client.post.side_effect = requests.Timeout("uncertain")
    monkeypatch.setattr(ex.requests, "Session", lambda: client)
    with pytest.raises((requests.Timeout, AssertionError, ValueError)):
        executor.submit(op)
    assert client.post.call_count == 1
    assert op.stage == (ex.LighterOperationStage.rejected if failure == "rejected" else ex.LighterOperationStage.unknown)
    with pytest.raises(AssertionError):
        executor.submit(op)


def test_restart_reconciliation_and_timeout(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor) -> None:
    """Restart uses the original saved hash; unconfirmed results remain blocked."""
    op = executor.prepare("burn_shares", 10, 42)
    saved = ex.LighterOperation.from_dict(op.as_dict())
    monkeypatch.setattr(ex, "fetch_operation_status", lambda *_: None)
    with pytest.raises(TimeoutError):
        executor.reconcile(saved, timeout=0)
    with pytest.raises(AssertionError):
        executor.prepare("mint_shares", 1, 42)
    monkeypatch.setattr(ex, "fetch_operation_status", lambda *_: receipt(saved))
    executor.reconcile(saved)
    assert saved.stage == ex.LighterOperationStage.confirmed


@pytest.mark.parametrize("http_status", [400, 500])
def test_http_rejection_differs_from_uncertain_server_failure(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor, http_status: int) -> None:
    """Explicit HTTP 400 rejection unblocks the key; a server failure does not."""
    op = executor.prepare("mint_shares", 4, 42)
    response = requests.Response()
    response.status_code = http_status
    response._content = b'{"code": 21104, "message": "invalid nonce"}'
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response
    monkeypatch.setattr(ex.requests, "Session", lambda: client)
    with pytest.raises((ValueError, requests.HTTPError)):
        executor.submit(op)
    assert client.post.call_count == 1
    if http_status == 400:
        assert op.stage == ex.LighterOperationStage.rejected
        assert op.receipt["code"] == 21104
        executor.prepare("mint_shares", 4, 42)
    else:
        assert op.stage == ex.LighterOperationStage.unknown
        with pytest.raises(AssertionError, match="Reconcile"):
            executor.prepare("mint_shares", 4, 42)


def test_unrecognised_status_remains_pending(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor) -> None:
    """An unfamiliar receipt status never becomes success or permits replay."""
    op = executor.prepare("mint_shares", 4, 42)
    monkeypatch.setattr(ex, "fetch_operation_status", lambda *_: receipt(op, status=99))
    with pytest.raises(TimeoutError):
        executor.reconcile(op, timeout=0)
    assert op.stage == ex.LighterOperationStage.unknown


def test_malformed_submission_response_keeps_original_pending(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor) -> None:
    """A missing API result code cannot release the account for another mint."""
    op = executor.prepare("mint_shares", 4, 42)
    response = requests.Response()
    response.status_code = 200
    response._content = b"{}"
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response
    monkeypatch.setattr(ex.requests, "Session", lambda: client)
    with pytest.raises(AssertionError, match="Malformed"):
        executor.submit(op)
    assert op.stage == ex.LighterOperationStage.unknown
    with pytest.raises(AssertionError, match="Reconcile"):
        executor.prepare("mint_shares", 4, 42)


def test_status_checks_nonce_account_and_hash(session: Mock, executor: ex.LighterPoolExecutor) -> None:
    """Receipt decoding uses flat API shape and rejects a foreign nonce."""
    op = executor.prepare("burn_shares", 4, 42)
    session.get.return_value.json.return_value = receipt(op)
    assert ex.fetch_operation_status(session, op)["nonce"] == op.nonce
    session.get.return_value.json.return_value = {**receipt(op), "nonce": 100}
    with pytest.raises(AssertionError):
        ex.fetch_operation_status(session, op)


def test_matching_withdrawal_paginates_and_uses_exact_id(monkeypatch: pytest.MonkeyPatch, session: Mock) -> None:
    """No similar amount or timestamp can select another withdrawal."""
    pages = iter([{"withdraws": [{"id": "other"}], "cursor": "page2"}, {"withdraws": [{"id": "wanted", "type": "secure", "asset_id": 3, "amount": "0.9999", "status": "claimable"}], "cursor": ""}])
    monkeypatch.setattr(ex, "fetch_withdrawal_history", lambda *_: next(pages))
    row = ex.fetch_matching_withdrawal(session, 123, "wanted", "secret")
    assert row["amount"] == "0.9999"


def test_wait_claimable_uses_actual_amount(monkeypatch: pytest.MonkeyPatch, session: Mock) -> None:
    """Claimable proceeds can differ from the originally requested collateral."""
    monkeypatch.setattr(ex, "fetch_matching_withdrawal", lambda *_: {"status": "claimable", "amount": "0.998"})
    assert ex.wait_for_claimable_withdrawal(session, 123, "id", lambda: "secret")["amount"] == "0.998"
    monkeypatch.setattr(ex, "fetch_matching_withdrawal", lambda *_: {"status": "pending", "amount": "1"})
    with pytest.raises(TimeoutError):
        ex.wait_for_claimable_withdrawal(session, 123, "id", lambda: "secret", timeout=0)


def test_withdrawal_snapshot_includes_older_pending_requests(monkeypatch: pytest.MonkeyPatch, session: Mock) -> None:
    """The round-trip baseline must include pending rows beyond the first page."""
    pages = iter([{"withdraws": [{"id": "recent", "status": "completed"}], "cursor": "older"}, {"withdraws": [{"id": "old", "status": "pending"}], "cursor": ""}])
    monkeypatch.setattr(ex, "fetch_withdrawal_history", lambda *_: next(pages))
    rows = tuple(ex.fetch_withdrawals(session, 123, "secret"))
    assert [row["id"] for row in rows] == ["recent", "old"]
    assert any(row["status"] == "pending" for row in rows)


@pytest.mark.parametrize("status", ["failed", "refunded", "completed"])
def test_wait_claimable_stops_on_terminal_status(monkeypatch: pytest.MonkeyPatch, session: Mock, status: str) -> None:
    """A terminal history record needs reconciliation instead of more polling."""
    monkeypatch.setattr(ex, "fetch_matching_withdrawal", lambda *_: {"status": status, "amount": "1"})
    with pytest.raises(AssertionError, match="reconcile"):
        ex.wait_for_claimable_withdrawal(session, 123, "id", lambda: "secret")


def test_withdrawal_pagination_loop_fails(monkeypatch: pytest.MonkeyPatch, session: Mock) -> None:
    """Malformed provider pagination cannot make a history scan loop forever."""
    monkeypatch.setattr(ex, "fetch_withdrawal_history", lambda *_: {"withdraws": [], "cursor": "same"})
    with pytest.raises(AssertionError, match="pagination loop"):
        tuple(ex.fetch_withdrawals(session, 123, "secret"))


def test_bootstrap_conservation_rejects_double_counting() -> None:
    """Fees are added to assets, while received bridges replace receivables."""
    ex.reconcile_bootstrap(Decimal(20), (Decimal("18.99"), Decimal("0.009")), Decimal(1), Decimal(0), Decimal("0.001"))
    with pytest.raises(AssertionError):
        ex.reconcile_bootstrap(Decimal(20), (Decimal("18.99"), Decimal("0.009")), Decimal(1), Decimal("1.009"), Decimal("0.001"))
    with pytest.raises(AssertionError, match="unavailable"):
        ex.reconcile_bootstrap(Decimal(20), (Decimal(19),), None, Decimal(0), Decimal(0))


def test_official_signer_preserves_integer_share_amounts() -> None:
    """Sign with the native optional SDK in a process without pytest's package shadow."""
    code = """
import asyncio
import json
import sys
try:
    import lighter
except ImportError:
    sys.exit(77)
from eth_defi.lighter.api_key import generate_lighter_api_key
async def sign():
    key = generate_lighter_api_key()
    client = lighter.SignerClient(url='https://mainnet.zklighter.elliot.ai', account_index=123,
        api_private_keys={4: key.private_key},
        nonce_management_type=lighter.nonce_manager.NonceManagerType.NONE)
    assert client.chain_id == 304
    try:
        for method in (client.sign_mint_shares, client.sign_burn_shares):
            tx_type, payload, tx_hash, error = method(281474976710654, 7, nonce=0, api_key_index=4)
            assert error is None and tx_hash and tx_type > 0, error
            data = json.loads(payload)
            assert data['ShareAmount'] == 7
            assert data['PublicPoolIndex'] == 281474976710654
            assert data['AccountIndex'] == 123 and data['ApiKeyIndex'] == 4
        from eth_defi.lighter.execution import LighterPoolExecutor, LighterAccountIdentity
        from eth_defi.lighter.session import LighterSession
        executor = LighterPoolExecutor(LighterSession(), LighterAccountIdentity(123), client, lambda _: None)
        executor._next_nonce = 0
        op = executor.prepare('withdraw', 1000000)
        assert op.amount == 1000000
        assert json.loads(op.tx_info)['FromAccountIndex'] == 123
    finally:
        await client.close()
asyncio.run(sign())
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[2], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    if result.returncode == 77:
        pytest.skip("Optional official Lighter SDK is not installed")
    assert result.returncode == 0, result.stderr


def test_secure_withdrawal_uses_raw_usdc_and_available_balance(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor) -> None:
    """A withdrawal signs canonical monetary units rather than share quantities."""
    token = SimpleNamespace(address=ex.LIGHTER_USDC_ETHEREUM, convert_to_raw=lambda v: int(v * 10**6), convert_to_decimals=lambda v: Decimal(v) / 10**6)
    monkeypatch.setattr(ex, "fetch_execution_account", lambda *_: ({}, SimpleNamespace(available_balance=Decimal(2))))
    operation = ex.prepare_secure_withdrawal(executor, token, Decimal(1))
    assert operation.amount == 1_000_000
    executor.signer.sign_withdraw.assert_called_once_with(3, 0, 1_000_000, nonce=7, api_key_index=4)


def test_full_exit_from_zero_equity(pool: ex.LighterPoolState, holding: ex.LighterPoolHolding) -> None:
    """Explicitly reported zero equity is distinct from missing valuation data."""
    empty = replace(pool, equity=Decimal(0), available_balance=Decimal(0), status=1)
    assert ex.prepare_pool_amount(empty, Decimal(0), available_collateral=Decimal(0), holding=holding, full_exit=True) == holding.shares


def test_receipt_failure_does_not_confirm(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor) -> None:
    """A sequencer rejection remains a rejected operation and never a fill."""
    op = executor.prepare("mint_shares", 4, 42)
    monkeypatch.setattr(ex, "fetch_operation_status", lambda *_: receipt(op, status=0))
    with pytest.raises(ValueError, match="execution failed"):
        executor.reconcile(op)
    assert op.stage == ex.LighterOperationStage.rejected


def test_wrong_signed_account_is_rejected(executor: ex.LighterPoolExecutor) -> None:
    """An accidentally bound signer cannot emit an operation for another account."""
    executor.signer.sign_mint_shares.side_effect = None
    executor.signer.sign_mint_shares.return_value = (18, json.dumps({"AccountIndex": 999, "ApiKeyIndex": 4, "Nonce": 7, "ShareAmount": 4, "PublicPoolIndex": 42}), "hash", None)
    with pytest.raises(AssertionError):
        executor.prepare("mint_shares", 4, 42)
    executor.persist.assert_not_called()


def test_redemption_reads_only_the_held_target_pool(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor, account: dict, pool: ex.LighterPoolState) -> None:
    """An unrelated unavailable pool does not prevent exiting another holding."""
    account["shares"] = [{"public_pool_index": 42, "shares_amount": 10, "principal_amount": "20", "entry_usdc": "20", "entry_timestamp": 1700000000}, {"public_pool_index": 43, "shares_amount": 1, "principal_amount": "1", "entry_usdc": "1", "entry_timestamp": 1700000000}]
    monkeypatch.setattr(ex, "fetch_execution_account", lambda *_: (account, None))
    read_pool = Mock(return_value=replace(pool, status=1))
    monkeypatch.setattr(ex, "fetch_pool_state", read_pool)
    operation = ex.prepare_pool_redemption(executor, 42)
    assert operation.amount == 10 and operation.kind == "burn_shares"
    read_pool.assert_called_once_with(executor.session, 42)


def test_deposit_checks_current_account_and_pool(monkeypatch: pytest.MonkeyPatch, executor: ex.LighterPoolExecutor, account: dict, pool: ex.LighterPoolState) -> None:
    """The public entry helper fetches balances and signs its validated share size."""
    monkeypatch.setattr(ex, "fetch_execution_account", lambda *_: (account, SimpleNamespace(available_balance=Decimal(20))))
    monkeypatch.setattr(ex, "fetch_pool_state", lambda *_: pool)
    operation = ex.prepare_pool_deposit(executor, 42, Decimal(10))
    assert operation.amount == 4 and operation.pool_index == pool.pool_index
